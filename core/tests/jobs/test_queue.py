"""The jobs queue: enqueue, claim, heartbeats, checkpoints (spec 16.1)."""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from townrecord.db import connect
from townrecord.jobs import (
    DONE,
    PAUSED,
    QUEUED,
    RUNNING,
    Claim,
    ClaimLost,
    JobContext,
    JobPaused,
    JobsSettings,
    Registry,
    claim,
    enqueue,
    finish,
    get,
    heartbeat,
    pause,
    read_checkpoint,
    requeue_stale,
    return_to_queue,
    save_checkpoint,
)
from townrecord.jobs.queue import STALE_REASON

START = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """A clock the test moves by hand, so no test sleeps for real minutes."""

    def __init__(self, start: datetime = START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def context(conn: sqlite3.Connection, taken: Claim, clock: FakeClock) -> JobContext:
    return JobContext(
        conn=conn,
        job_id=taken.job_id,
        kind=taken.kind,
        payload=taken.payload,
        lane=taken.lane,
        claim_token=taken.token,
        clock=clock,
    )


def test_enqueue_is_one_insert(conn: sqlite3.Connection) -> None:
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    job_id = enqueue(conn, "scan", {"body": "city-council"})
    conn.set_trace_callback(None)

    assert job_id > 0
    assert len(statements) == 1, statements
    assert statements[0].lstrip().upper().startswith("INSERT")
    row = get(conn, job_id)
    assert row is not None
    assert row["state"] == QUEUED
    assert row["lane"] == "normal"
    assert row["attempts"] == 0
    assert row["claim_token"] is None


def test_enqueue_keeps_the_payload(conn: sqlite3.Connection) -> None:
    job_id = enqueue(conn, "scan", {"bodies": [7, 8], "deep": {"ok": True}})
    taken = claim(conn, "normal", "worker-1")
    assert taken is not None
    assert taken.job_id == job_id
    assert taken.payload == {"bodies": [7, 8], "deep": {"ok": True}}


def test_enqueue_uses_the_lane_registered_for_the_kind(conn: sqlite3.Connection) -> None:
    registry = Registry()
    registry.register("draft", lambda ctx: None, lane="heavy")
    job_id = enqueue(conn, "draft", None, registry=registry)
    assert get(conn, job_id)["lane"] == "heavy"


def test_enqueue_takes_the_lane_the_caller_gives(conn: sqlite3.Connection) -> None:
    job_id = enqueue(conn, "scan", None, lane="heavy")
    assert get(conn, job_id)["lane"] == "heavy"


def test_enqueue_refuses_a_lane_the_table_does_not_know(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        enqueue(conn, "scan", None, lane="editorial")


def test_claim_takes_the_oldest_queued_job_of_the_lane(conn: sqlite3.Connection) -> None:
    first = enqueue(conn, "scan")
    second = enqueue(conn, "scan")
    taken = claim(conn, "normal", "worker-1")
    assert taken is not None
    assert taken.job_id == first
    assert get(conn, second)["state"] == QUEUED


def test_claim_marks_the_job_running_with_a_token_and_a_heartbeat(
    conn: sqlite3.Connection,
) -> None:
    clock = FakeClock()
    job_id = enqueue(conn, "scan", clock=clock)
    taken = claim(conn, "normal", "worker-1", clock=clock)
    assert taken is not None
    row = get(conn, job_id)
    assert row["state"] == RUNNING
    assert row["claim_token"] == taken.token
    assert row["claimed_at"] == "2026-09-27T12:00:00.000000Z"
    assert row["heartbeat_at"] == row["claimed_at"]
    assert row["started_at"] == row["claimed_at"]


def test_claim_returns_none_when_the_lane_has_nothing_queued(conn: sqlite3.Connection) -> None:
    assert claim(conn, "normal", "worker-1") is None


def test_claim_leaves_the_other_lane_alone(conn: sqlite3.Connection) -> None:
    heavy = enqueue(conn, "draft", None, lane="heavy")
    assert claim(conn, "normal", "worker-1") is None
    assert get(conn, heavy)["state"] == QUEUED


def test_two_claimers_never_get_the_same_job(db_path: Path, conn: sqlite3.Connection) -> None:
    settings = JobsSettings(lane_limits={"heavy": 1, "normal": 100})
    total = 20
    wanted = {enqueue(conn, "scan") for _ in range(total)}

    barrier = threading.Barrier(6)
    found: list[int] = []
    guard = threading.Lock()

    def worker(name: str) -> None:
        own = connect(db_path)
        try:
            barrier.wait(timeout=10)
            while True:
                taken = claim(own, "normal", name, settings=settings)
                if taken is None:
                    return
                with guard:
                    found.append(taken.job_id)
        finally:
            own.close()

    threads = [threading.Thread(target=worker, args=(f"w-{i}",)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert len(found) == total, f"6 claimers took {len(found)} of {total} jobs"
    assert len(set(found)) == total, "one job was claimed twice"
    assert set(found) == wanted


def test_the_heavy_lane_never_runs_two_jobs_at_once(conn: sqlite3.Connection) -> None:
    first = enqueue(conn, "draft", None, lane="heavy")
    second = enqueue(conn, "draft", None, lane="heavy")
    assert claim(conn, "heavy", "worker-1") is not None
    assert claim(conn, "heavy", "worker-2") is None
    assert get(conn, first)["state"] == RUNNING
    assert get(conn, second)["state"] == QUEUED


def test_the_normal_lane_runs_two_jobs_at_once(conn: sqlite3.Connection) -> None:
    for _ in range(3):
        enqueue(conn, "scan")
    assert claim(conn, "normal", "worker-1") is not None
    assert claim(conn, "normal", "worker-2") is not None
    assert claim(conn, "normal", "worker-3") is None


def test_transcribe_never_runs_twice_even_across_lanes(conn: sqlite3.Connection) -> None:
    settings = JobsSettings(lane_limits={"heavy": 4, "normal": 4})
    enqueue(conn, "transcribe", None, lane="heavy")
    enqueue(conn, "transcribe", None, lane="normal")
    assert claim(conn, "heavy", "worker-1", settings=settings) is not None
    assert claim(conn, "normal", "worker-2", settings=settings) is None


def test_claim_skips_a_blocked_kind_and_takes_the_next_job(conn: sqlite3.Connection) -> None:
    settings = JobsSettings(lane_limits={"heavy": 1, "normal": 4})
    enqueue(conn, "transcribe", {"n": 1})
    other = enqueue(conn, "scan", {"n": 2})
    assert claim(conn, "normal", "worker-1", settings=settings) is not None
    taken = claim(conn, "normal", "worker-2", settings=settings)
    assert taken is not None
    assert taken.job_id == other
    assert taken.kind == "scan"


def test_a_stale_heartbeat_returns_the_job_with_its_checkpoint(conn: sqlite3.Connection) -> None:
    clock = FakeClock()
    settings = JobsSettings(heartbeat_timeout=60.0)
    job_id = enqueue(conn, "capture", clock=clock)
    taken = claim(conn, "normal", "worker-1", settings=settings, clock=clock)
    assert taken is not None
    save_checkpoint(conn, job_id, taken.token, {"part": 4}, clock=clock)

    clock.advance(59.0)
    assert requeue_stale(conn, settings=settings, clock=clock) == []
    assert get(conn, job_id)["state"] == RUNNING

    clock.advance(2.0)
    assert requeue_stale(conn, settings=settings, clock=clock) == [job_id]
    row = get(conn, job_id)
    assert row["state"] == QUEUED
    assert row["attempts"] == 1
    assert row["claim_token"] is None
    assert row["last_error"] == STALE_REASON
    assert read_checkpoint(conn, job_id) == {"part": 4}


def test_a_running_job_with_no_heartbeat_is_reclaimed(conn: sqlite3.Connection) -> None:
    job_id = enqueue(conn, "capture")
    conn.execute("UPDATE jobs SET state = 'running', claim_token = 'x' WHERE id = ?", (job_id,))
    assert requeue_stale(conn) == [job_id]
    assert get(conn, job_id)["state"] == QUEUED


def test_a_stale_job_resumes_from_its_checkpoint(conn: sqlite3.Connection) -> None:
    clock = FakeClock()
    settings = JobsSettings(heartbeat_timeout=60.0)
    job_id = enqueue(conn, "capture", clock=clock)
    first = claim(conn, "normal", "worker-1", settings=settings, clock=clock)
    assert first is not None
    context(conn, first, clock).save_checkpoint({"page": 2, "done": False})

    clock.advance(120.0)
    requeue_stale(conn, settings=settings, clock=clock)

    second = claim(conn, "normal", "worker-2", settings=settings, clock=clock)
    assert second is not None
    assert second.job_id == job_id
    assert context(conn, second, clock).checkpoint() == {"page": 2, "done": False}


def test_a_late_write_from_a_stale_worker_is_refused(conn: sqlite3.Connection) -> None:
    clock = FakeClock()
    settings = JobsSettings(heartbeat_timeout=60.0)
    job_id = enqueue(conn, "capture", clock=clock)
    old = claim(conn, "normal", "worker-1", settings=settings, clock=clock)
    assert old is not None
    stale_context = context(conn, old, clock)
    stale_context.save_checkpoint({"page": 1})

    clock.advance(120.0)
    requeue_stale(conn, settings=settings, clock=clock)
    fresh = claim(conn, "normal", "worker-2", settings=settings, clock=clock)
    assert fresh is not None
    assert fresh.token != old.token

    with pytest.raises(ClaimLost):
        stale_context.save_checkpoint({"page": 99})
    with pytest.raises(ClaimLost):
        stale_context.heartbeat()
    with pytest.raises(ClaimLost):
        finish(conn, job_id, old.token, DONE)

    row = get(conn, job_id)
    assert row["claim_token"] == fresh.token
    assert row["state"] == RUNNING
    assert read_checkpoint(conn, job_id) == {"page": 1}


def test_a_late_write_after_a_finished_job_is_refused(conn: sqlite3.Connection) -> None:
    job_id = enqueue(conn, "scan")
    taken = claim(conn, "normal", "worker-1")
    assert taken is not None
    finish(conn, job_id, taken.token, DONE)
    with pytest.raises(ClaimLost):
        save_checkpoint(conn, job_id, taken.token, {"late": True})
    with pytest.raises(ClaimLost):
        heartbeat(conn, job_id, taken.token)


def test_finish_records_the_state_and_the_time(conn: sqlite3.Connection) -> None:
    clock = FakeClock()
    job_id = enqueue(conn, "scan", clock=clock)
    taken = claim(conn, "normal", "worker-1", clock=clock)
    assert taken is not None
    clock.advance(5.0)
    finish(conn, job_id, taken.token, DONE, clock=clock)
    row = get(conn, job_id)
    assert row["state"] == DONE
    assert row["finished_at"] == "2026-09-27T12:00:05.000000Z"
    assert row["last_error"] is None
    assert row["claim_token"] is None


def test_pause_keeps_the_plain_reason(conn: sqlite3.Connection) -> None:
    clock = FakeClock()
    job_id = enqueue(conn, "capture", clock=clock)
    taken = claim(conn, "normal", "worker-1", clock=clock)
    assert taken is not None
    with pytest.raises(JobPaused) as caught:
        context(conn, taken, clock).pause("The YouTube account is not signed in.")
    assert "not signed in" in str(caught.value)
    row = get(conn, job_id)
    assert row["state"] == PAUSED
    assert row["last_error"] == "The YouTube account is not signed in."
    assert row["claim_token"] is None
    with pytest.raises(ClaimLost):
        save_checkpoint(conn, job_id, taken.token, {"late": True})


def test_pause_without_a_reason_is_refused(conn: sqlite3.Connection) -> None:
    job_id = enqueue(conn, "capture")
    taken = claim(conn, "normal", "worker-1")
    assert taken is not None
    with pytest.raises(ValueError):
        pause(conn, job_id, taken.token, "   ")
    assert get(conn, job_id)["state"] == RUNNING


def test_return_to_queue_keeps_the_checkpoint(conn: sqlite3.Connection) -> None:
    job_id = enqueue(conn, "capture")
    taken = claim(conn, "normal", "worker-1")
    assert taken is not None
    save_checkpoint(conn, job_id, taken.token, {"page": 3})
    return_to_queue(conn, job_id, taken.token, reason="The runner was stopping.")
    row = get(conn, job_id)
    assert row["state"] == QUEUED
    assert row["attempts"] == 0
    assert read_checkpoint(conn, job_id) == {"page": 3}


def test_a_second_handler_for_one_kind_is_refused() -> None:
    registry = Registry()
    registry.register("scan", lambda ctx: None)
    with pytest.raises(ValueError):
        registry.register("scan", lambda ctx: None)


def test_a_handler_on_an_unknown_lane_is_refused() -> None:
    registry = Registry()
    with pytest.raises(ValueError):
        registry.register("scan", lambda ctx: None, lane="editorial")


def test_a_kind_with_no_handler_has_no_lane() -> None:
    registry = Registry()
    assert registry.handler_for("nothing") is None
    assert registry.lane_for("nothing") is None
    assert registry.kinds() == ()
