"""The jobs queue (spec 16.1, decision 10 rule 1).

A request calls `enqueue`, which is one INSERT, and returns at once. A worker
claims a job later. `claim` takes the oldest queued job of a lane, marks it
running and gives it a fresh random claim token. Every write after that
carries the token, so a worker that lost its claim cannot overwrite the newer
run.

A job that runs too long without a heartbeat is put back in the queue with its
checkpoint kept, so the next run resumes where the last one stopped.

`attempts` counts the times a job had to be recovered and put back in the
queue (spec 16.1). Nothing else changes it.
"""

from __future__ import annotations

import contextlib
import json
import logging
import secrets
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .registry import default_registry
from .settings import JobsSettings

logger = logging.getLogger(__name__)

#: A function that returns the current time. Tests pass a clock they control.
Clock = Callable[[], datetime]

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
PAUSED = "paused"

#: Every state the table accepts, in the order of a normal run.
STATES = (QUEUED, RUNNING, DONE, FAILED, PAUSED)

#: States that a finished job may end in.
FINISHED_STATES = (DONE, FAILED, PAUSED)

#: The longest plain reason kept in `last_error`. A stack trace never goes here.
MAX_REASON = 300

#: Why a job went back to the queue when its heartbeat stopped.
STALE_REASON = "The worker stopped responding, so the job was put back in the queue."

#: Why a job went back to the queue when the runner was stopping.
INTERRUPTED_REASON = "The runner was stopping, so the job was put back in the queue."


def utcnow() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


def stamp(moment: datetime) -> str:
    """Return a UTC timestamp string that also sorts in time order.

    A naive datetime is read as UTC, so a caller cannot silently introduce a
    local time that sorts wrongly against the others.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def short_reason(exc: BaseException) -> str:
    """Turn an exception into one short plain line, without a stack trace."""
    text = " ".join(str(exc).split())
    name = type(exc).__name__
    reason = f"{name}: {text}" if text else name
    return reason[:MAX_REASON]


class ClaimLost(RuntimeError):
    """A write was refused because another worker holds the claim now."""


class JobNotPaused(RuntimeError):
    """A job was asked to come back to the queue, and it was not paused."""


class JobPaused(Exception):
    """Raised by JobContext.pause, after the job is recorded as paused."""


class JobInterrupted(Exception):
    """Raised to put an unfinished job back in the queue with its checkpoint."""


class JobDeferred(Exception):
    """Raised by JobContext.defer, after the job is queued again with a delay.

    The job is not finished and it is not broken: something outside it has to
    change first. It goes back in the queue with `run_after` set and its
    checkpoint kept, so no work is lost (spec 8.2, 8.3).
    """


def _encode(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


def _decode(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        # A row written by hand or by an older version. Hand back what is there
        # rather than pretending the job has no payload.
        return text


def _moment(clock: Clock) -> str:
    return stamp(clock())


@dataclass(frozen=True)
class Claim:
    """One claimed job, as the worker sees it."""

    job_id: int
    kind: str
    payload: Any
    lane: str
    token: str
    attempts: int


def new_claim_token(worker_id: str) -> str:
    """Return a fresh random claim token for one run of one job."""
    return f"{worker_id}.{secrets.token_urlsafe(16)}"


def enqueue(
    conn: sqlite3.Connection,
    kind: str,
    payload: Any = None,
    lane: str | None = None,
    *,
    registry: Any = None,
    clock: Clock = utcnow,
) -> int:
    """Add a job to the queue and return its id. This is one INSERT, no work.

    `lane` None means the lane the kind is registered under, or `normal` when
    the kind is unknown. An API route calls this and returns at once
    (decision 10 rule 1).
    """
    chosen = lane
    if chosen is None:
        known = (default_registry if registry is None else registry).lane_for(kind)
        chosen = known or "normal"
    cursor = conn.execute(
        "INSERT INTO jobs (kind, payload, lane, state, attempts, created_at) "
        "VALUES (?, ?, ?, ?, 0, ?)",
        (kind, _encode(payload), chosen, QUEUED, _moment(clock)),
    )
    return int(cursor.lastrowid or 0)


def get(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    """Return one job row, or None when there is no such job."""
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    with contextlib.suppress(sqlite3.Error):  # nothing to roll back
        conn.execute("ROLLBACK")


def _lane_is_full(conn: sqlite3.Connection, lane: str, settings: JobsSettings) -> bool:
    running = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE lane = ? AND state = ?", (lane, RUNNING)
    ).fetchone()[0]
    return int(running) >= settings.lane_limit(lane)


def _kind_is_full(conn: sqlite3.Connection, kind: str, settings: JobsSettings) -> bool:
    limit = settings.kind_limit(kind)
    if limit is None:
        return False
    running = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE kind = ? AND state = ?", (kind, RUNNING)
    ).fetchone()[0]
    return int(running) >= limit


def _next_startable(
    conn: sqlite3.Connection, lane: str, settings: JobsSettings, now: str
) -> sqlite3.Row | None:
    """Return the oldest queued job of the lane that no limit blocks.

    A job whose ``run_after`` is still in the future is not due yet: it stays
    queued and the worker looks at the jobs behind it (spec 8.2, "retry later").
    """
    cursor = conn.execute(
        "SELECT id, kind, payload, lane, attempts FROM jobs "
        "WHERE lane = ? AND state = ? AND (run_after IS NULL OR run_after <= ?) ORDER BY id",
        (lane, QUEUED, now),
    )
    for row in cursor:
        if not _kind_is_full(conn, row["kind"], settings):
            return row
    return None


def claim(
    conn: sqlite3.Connection,
    lane: str,
    worker_id: str,
    *,
    settings: JobsSettings | None = None,
    clock: Clock = utcnow,
) -> Claim | None:
    """Claim the oldest queued job of the lane, or return None.

    The read and the write happen inside one `BEGIN IMMEDIATE` transaction and
    the UPDATE repeats `state = 'queued'`, so two claimers can never take the
    same job. The lane limit and the per-kind limit are checked here too, so a
    limit holds even when a second process runs its own workers.
    """
    settings = settings or JobsSettings()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if _lane_is_full(conn, lane, settings):
            conn.execute("COMMIT")
            return None
        token = new_claim_token(worker_id)
        moment = _moment(clock)
        row = _next_startable(conn, lane, settings, moment)
        if row is None:
            conn.execute("COMMIT")
            return None
        cursor = conn.execute(
            "UPDATE jobs SET state = ?, claim_token = ?, claimed_at = ?, heartbeat_at = ?, "
            "started_at = ?, last_error = NULL, run_after = NULL "
            "WHERE id = ? AND state = ?",
            (RUNNING, token, moment, moment, moment, row["id"], QUEUED),
        )
        if cursor.rowcount != 1:
            conn.execute("COMMIT")
            return None
        conn.execute("COMMIT")
    except BaseException:
        _rollback_quietly(conn)
        raise
    return Claim(
        job_id=int(row["id"]),
        kind=str(row["kind"]),
        payload=_decode(row["payload"]),
        lane=str(row["lane"]),
        token=token,
        attempts=int(row["attempts"]),
    )


def _guarded(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple[Any, ...],
    job_id: int,
    token: str,
) -> None:
    cursor = conn.execute(sql, (*params, job_id, token))
    if cursor.rowcount != 1:
        raise ClaimLost(f"Job {job_id} is no longer claimed by this worker.")


def heartbeat(conn: sqlite3.Connection, job_id: int, token: str, *, clock: Clock = utcnow) -> None:
    """Say that the job is still alive. Raises ClaimLost when it is not ours."""
    _guarded(
        conn,
        "UPDATE jobs SET heartbeat_at = ? WHERE id = ? AND claim_token = ? AND state = 'running'",
        (_moment(clock),),
        job_id,
        token,
    )


def save_checkpoint(
    conn: sqlite3.Connection, job_id: int, token: str, value: Any, *, clock: Clock = utcnow
) -> None:
    """Save where the job got to, so a later run resumes instead of restarting."""
    _guarded(
        conn,
        "UPDATE jobs SET checkpoint = ? WHERE id = ? AND claim_token = ? AND state = 'running'",
        (_encode(value),),
        job_id,
        token,
    )


def read_checkpoint(conn: sqlite3.Connection, job_id: int) -> Any:
    """Return the saved checkpoint of a job, or None when it has none."""
    row = conn.execute("SELECT checkpoint FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return None if row is None else _decode(row["checkpoint"])


def finish(
    conn: sqlite3.Connection,
    job_id: int,
    token: str,
    state: str,
    *,
    reason: str | None = None,
    clock: Clock = utcnow,
) -> None:
    """Close a job as done or failed. Raises ClaimLost when it is not ours."""
    if state not in (DONE, FAILED):
        raise ValueError("A job is finished as done or failed.")
    _guarded(
        conn,
        "UPDATE jobs SET state = ?, last_error = ?, claim_token = NULL, claimed_at = NULL, "
        "heartbeat_at = NULL, finished_at = ? WHERE id = ? AND claim_token = ?",
        (state, None if reason is None else reason[:MAX_REASON], _moment(clock)),
        job_id,
        token,
    )


def pause(
    conn: sqlite3.Connection, job_id: int, token: str, reason: str, *, clock: Clock = utcnow
) -> None:
    """Record that the job cannot run, then raise JobPaused.

    The reason is kept in `last_error` so the user can see why. A paused job
    never skips silently (spec 16.2).
    """
    text = " ".join(str(reason).split())[:MAX_REASON]
    if not text:
        raise ValueError("A paused job needs a plain reason.")
    _guarded(
        conn,
        "UPDATE jobs SET state = ?, last_error = ?, claim_token = NULL, claimed_at = NULL, "
        "heartbeat_at = NULL WHERE id = ? AND claim_token = ? AND state = 'running'",
        (PAUSED, text),
        job_id,
        token,
    )
    raise JobPaused(text)


def return_to_queue(
    conn: sqlite3.Connection,
    job_id: int,
    token: str,
    *,
    reason: str,
    run_after: datetime | None = None,
    clock: Clock = utcnow,
) -> None:
    """Put an unfinished job back in the queue with its checkpoint kept.

    ``run_after`` is the earliest moment the job may be claimed again. A job
    that is only waiting for something outside it (a live stream ending, a rate
    limit lifting) says so here, so the runner does not spin on it (spec 8.2,
    8.3). None means the job is claimable at once.
    """
    _guarded(
        conn,
        "UPDATE jobs SET state = ?, last_error = ?, run_after = ?, claim_token = NULL, "
        "claimed_at = NULL, heartbeat_at = NULL "
        "WHERE id = ? AND claim_token = ? AND state = 'running'",
        (QUEUED, reason[:MAX_REASON], None if run_after is None else stamp(run_after)),
        job_id,
        token,
    )


def requeue_paused(conn: sqlite3.Connection, job_id: int, *, reason: str) -> None:
    """Put a paused job back in the queue, because what it waited for is here.

    A paused job holds no claim, so no worker can send it back the way a
    running job is sent back: the queue has to be asked to take it again. The
    reason is kept in `last_error` until the next run claims the job and clears
    it, which is what makes the pause visible in the meantime.

    `attempts` is not touched: that count is the times a run was lost and
    recovered (spec 16.1), and a job that never ran was not lost. Raises
    :class:`JobNotPaused` when the job is not paused, so a caller that raced
    another one finds out instead of writing over a run in progress.
    """
    text = " ".join(str(reason).split())
    if not text:
        raise ValueError("A job asked back into the queue needs a plain reason.")
    cursor = conn.execute(
        "UPDATE jobs SET state = ?, last_error = ?, claim_token = NULL, claimed_at = NULL, "
        "heartbeat_at = NULL WHERE id = ? AND state = ?",
        (QUEUED, text[:MAX_REASON], job_id, PAUSED),
    )
    if cursor.rowcount != 1:
        raise JobNotPaused(f"Job {job_id} is not paused, so it was not put back in the queue.")


def requeue_stale(
    conn: sqlite3.Connection,
    *,
    settings: JobsSettings | None = None,
    clock: Clock = utcnow,
) -> list[int]:
    """Put jobs back in the queue when their heartbeat stopped. Return their ids.

    The checkpoint is kept, so the next run resumes (spec 16.1). `attempts`
    grows by one, which is how the lost run is recorded.
    """
    settings = settings or JobsSettings()
    cutoff = stamp(clock() - timedelta(seconds=settings.heartbeat_timeout))
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE state = ? AND (heartbeat_at IS NULL OR heartbeat_at < ?)",
            (RUNNING, cutoff),
        ).fetchall()
        stale = [int(row["id"]) for row in rows]
        for job_id in stale:
            conn.execute(
                "UPDATE jobs SET state = ?, attempts = attempts + 1, last_error = ?, "
                "claim_token = NULL, claimed_at = NULL, heartbeat_at = NULL "
                "WHERE id = ? AND state = ?",
                (QUEUED, STALE_REASON, job_id, RUNNING),
            )
            logger.warning("Job %s stopped sending heartbeats and went back to the queue.", job_id)
        conn.execute("COMMIT")
    except BaseException:
        _rollback_quietly(conn)
        raise
    return stale


@dataclass
class JobContext:
    """What a handler gets: its payload, its checkpoint, and its claim.

    Every write goes through the claim token, so a worker that lost its claim
    finds out at once (ClaimLost) instead of overwriting the newer run.
    """

    conn: sqlite3.Connection
    job_id: int
    kind: str
    payload: Any
    lane: str
    claim_token: str
    clock: Clock = utcnow
    stop_event: threading.Event | None = field(default=None, repr=False)

    def checkpoint(self) -> Any:
        """Return the checkpoint saved by an earlier run, or None."""
        return read_checkpoint(self.conn, self.job_id)

    def save_checkpoint(self, value: Any) -> None:
        """Save where this run got to."""
        save_checkpoint(self.conn, self.job_id, self.claim_token, value, clock=self.clock)

    def heartbeat(self) -> None:
        """Say that this run is still alive."""
        heartbeat(self.conn, self.job_id, self.claim_token, clock=self.clock)

    def should_stop(self) -> bool:
        """Return True when the runner has been asked to shut down."""
        return self.stop_event is not None and self.stop_event.is_set()

    def pause(self, reason: str) -> None:
        """Record that the job cannot run now. Raises JobPaused and never returns."""
        pause(self.conn, self.job_id, self.claim_token, reason, clock=self.clock)

    def defer(self, reason: str, *, delay_s: float) -> None:
        """Queue this job again, no earlier than `delay_s` from now.

        The plain reason is kept in `last_error`, so the user can see why the
        job is waiting. Raises JobDeferred and never returns.
        """
        text = " ".join(str(reason).split())[:MAX_REASON]
        if not text:
            raise ValueError("A deferred job needs a plain reason.")
        run_after = self.clock() + timedelta(seconds=max(0.0, delay_s))
        return_to_queue(
            self.conn,
            self.job_id,
            self.claim_token,
            reason=text,
            run_after=run_after,
            clock=self.clock,
        )
        raise JobDeferred(text)

    def interrupt(self, reason: str | None = None) -> None:
        """Stop this run and leave the job resumable. Raises JobInterrupted."""
        raise JobInterrupted(reason or INTERRUPTED_REASON)
