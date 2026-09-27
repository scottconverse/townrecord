"""The jobs runner (spec 16.1, decision 10 rule 1).

The runner keeps one thread per lane worker: one for `heavy` and two for
`normal`. Each thread has its own SQLite connection, because a connection
belongs to the thread that made it. A shutdown request makes the workers stop
claiming; handlers see it through `JobContext.should_stop()`.

While a handler runs, the runner refreshes that job's heartbeat, so a long
capture is not mistaken for a dead worker.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ..db import connect as default_connect
from . import queue
from .queue import (
    Claim,
    ClaimLost,
    Clock,
    JobContext,
    JobDeferred,
    JobInterrupted,
    JobPaused,
    utcnow,
)
from .registry import Registry, default_registry
from .settings import JobsSettings

logger = logging.getLogger(__name__)

#: How long `stop` waits for the workers before it gives up on them.
DEFAULT_STOP_TIMEOUT = 5.0

#: How long a worker keeps trying to open its connection while the file is busy.
OPEN_RETRY_SECONDS = 2.0

#: What a job says when no handler was registered for its kind.
NO_HANDLER = "No handler is registered for job kind '{kind}'."


class Runner:
    """Runs the queued jobs of every lane in threads."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        registry: Registry | None = None,
        settings: JobsSettings | None = None,
        clock: Clock = utcnow,
        connect: Callable[[str | Path], sqlite3.Connection] = default_connect,
    ) -> None:
        self.db_path = Path(db_path)
        self._registry = default_registry if registry is None else registry
        self._settings = settings or JobsSettings()
        self._clock = clock
        self._connect = connect
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        #: job id -> (claim token, lane) for the jobs this runner is running.
        self._running: dict[int, tuple[str, str]] = {}
        self._lock = threading.Lock()
        self._last_reclaim = 0.0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start one thread per lane worker."""
        if self._threads:
            raise RuntimeError("This runner is already started.")
        self._stop.clear()
        for lane in self._settings.lanes():
            for index in range(self._settings.lane_limit(lane)):
                worker_id = f"{lane}-{index + 1}"
                self._threads.append(
                    self._spawn(
                        self._lane_loop, lane, worker_id, name=f"townrecord-jobs-{worker_id}"
                    )
                )
        if self._settings.heartbeat_interval > 0:
            self._threads.append(
                self._spawn(self._heartbeat_loop, name="townrecord-jobs-heartbeat")
            )

    def stop(self, timeout: float = DEFAULT_STOP_TIMEOUT) -> bool:
        """Ask the workers to stop and wait for them. True when all stopped.

        A worker that is inside a slow handler cannot be cut off. Its job keeps
        its checkpoint and is put back in the queue later, by the stale check or
        by the handler itself, so the work is not lost.
        """
        self._stop.set()
        deadline = time.monotonic() + timeout
        stopped = True
        for thread in list(self._threads):
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                stopped = False
                logger.warning("The job worker %s did not stop within the timeout.", thread.name)
        self._threads = [thread for thread in self._threads if thread.is_alive()]
        return stopped

    def _spawn(self, target: Callable[..., None], *args: object, name: str) -> threading.Thread:
        thread = threading.Thread(target=target, args=args, name=name, daemon=True)
        thread.start()
        return thread

    def _open(self) -> sqlite3.Connection:
        """Open this thread's own connection, waiting out a busy database.

        Several workers start at once, and the first of them turns on write
        ahead logging, which needs a moment alone with the file. A worker that
        gave up here would leave its lane unserved, so it tries again instead.
        """
        deadline = time.monotonic() + OPEN_RETRY_SECONDS
        while True:
            try:
                return self._connect(self.db_path)
            except sqlite3.OperationalError as exc:
                if time.monotonic() >= deadline:
                    raise
                logger.warning("The jobs database was busy (%s). Trying again.", exc)
                time.sleep(0.05)

    def running_jobs(self) -> tuple[int, ...]:
        """Return the ids of the jobs this runner is running right now."""
        with self._lock:
            return tuple(sorted(self._running))

    # -- the loops ---------------------------------------------------------

    def _lane_loop(self, lane: str, worker_id: str) -> None:
        conn = self._open()
        try:
            while not self._stop.is_set():
                self._reclaim_if_due(conn)
                taken = queue.claim(
                    conn, lane, worker_id, settings=self._settings, clock=self._clock
                )
                if taken is None:
                    self._stop.wait(self._settings.poll_interval)
                    continue
                self._run_claim(conn, taken)
        finally:
            conn.close()

    def _heartbeat_loop(self) -> None:
        conn = self._open()
        try:
            while not self._stop.wait(self._settings.heartbeat_interval):
                with self._lock:
                    live = list(self._running.items())
                for job_id, (token, _lane) in live:
                    self._beat(conn, job_id, token)
        finally:
            conn.close()

    def _beat(self, conn: sqlite3.Connection, job_id: int, token: str) -> None:
        try:
            queue.heartbeat(conn, job_id, token, clock=self._clock)
        except ClaimLost:
            self._forget(job_id)
        except sqlite3.Error as exc:  # a locked database must not kill the worker
            logger.warning("The heartbeat for job %s failed: %s", job_id, exc)

    def _reclaim_if_due(self, conn: sqlite3.Connection) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last_reclaim < self._settings.reclaim_interval:
                return
            self._last_reclaim = now
        try:
            queue.requeue_stale(conn, settings=self._settings, clock=self._clock)
        except sqlite3.Error as exc:
            logger.warning("The stale check failed: %s", exc)

    # -- one job -----------------------------------------------------------

    def run_once(self, lane: str = "normal") -> int | None:
        """Claim and run one job of the lane. Return its id, or None if there was none.

        Tests use this to run a job without timing. A live runner does the same
        work in its lane threads.
        """
        conn = self._open()
        try:
            taken = queue.claim(
                conn, lane, f"{lane}-run-once", settings=self._settings, clock=self._clock
            )
            if taken is None:
                return None
            self._run_claim(conn, taken)
            return taken.job_id
        finally:
            conn.close()

    def _track(self, job_id: int, token: str, lane: str) -> None:
        with self._lock:
            self._running[job_id] = (token, lane)

    def _forget(self, job_id: int) -> None:
        with self._lock:
            self._running.pop(job_id, None)

    def _run_claim(self, conn: sqlite3.Connection, taken: Claim) -> None:
        handler = self._registry.handler_for(taken.kind)
        context = JobContext(
            conn=conn,
            job_id=taken.job_id,
            kind=taken.kind,
            payload=taken.payload,
            lane=taken.lane,
            claim_token=taken.token,
            clock=self._clock,
            stop_event=self._stop,
            origin=taken.origin,
        )
        if handler is None:
            self._close(conn, taken, queue.FAILED, NO_HANDLER.format(kind=taken.kind))
            return
        self._track(taken.job_id, taken.token, taken.lane)
        try:
            handler(context)
        except JobPaused as exc:
            # pause() already wrote the state and the reason.
            logger.info("Job %s paused: %s", taken.job_id, exc)
        except JobDeferred as exc:
            # defer() already put the job back in the queue with its delay.
            logger.info("Job %s deferred: %s", taken.job_id, exc)
        except JobInterrupted as exc:
            self._requeue(conn, taken, str(exc))
        except ClaimLost:
            logger.warning(
                "Job %s: another worker holds the claim, so this result was dropped.",
                taken.job_id,
            )
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the worker
            logger.exception("Job %s (%s) failed", taken.job_id, taken.kind)
            self._close(conn, taken, queue.FAILED, queue.short_reason(exc))
        else:
            self._close(conn, taken, queue.DONE)
        finally:
            self._forget(taken.job_id)

    def _close(
        self, conn: sqlite3.Connection, taken: Claim, state: str, reason: str | None = None
    ) -> None:
        try:
            queue.finish(conn, taken.job_id, taken.token, state, reason=reason, clock=self._clock)
        except ClaimLost:
            logger.warning(
                "Job %s: another worker holds the claim, so its state was left alone.",
                taken.job_id,
            )

    def _requeue(self, conn: sqlite3.Connection, taken: Claim, reason: str) -> None:
        try:
            queue.return_to_queue(conn, taken.job_id, taken.token, reason=reason, clock=self._clock)
        except ClaimLost:
            logger.warning(
                "Job %s: another worker holds the claim, so it was left alone.", taken.job_id
            )
