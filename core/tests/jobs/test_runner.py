"""The jobs runner: lane threads, shutdown, heartbeats (spec 16.1)."""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from townrecord.jobs import (
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    JobContext,
    JobsSettings,
    Registry,
    Runner,
    claim,
    enqueue,
    get,
    requeue_stale,
)


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Wait for a condition that another thread brings about."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class Concurrency:
    """Counts how many handlers are inside their body at the same time."""

    def __init__(self, hold: float = 0.05) -> None:
        self.hold = hold
        self.live = 0
        self.peak = 0
        self.seen: list[str] = []
        self._lock = threading.Lock()

    def handler(self, ctx: JobContext) -> None:
        with self._lock:
            self.live += 1
            self.peak = max(self.peak, self.live)
            self.seen.append(ctx.kind)
        time.sleep(self.hold)
        with self._lock:
            self.live -= 1


def test_run_once_runs_one_job_and_marks_it_done(db_path: Path, conn: sqlite3.Connection) -> None:
    seen: list[object] = []
    registry = Registry()
    registry.register("scan", lambda ctx: seen.append(ctx.payload))
    runner = Runner(db_path, registry=registry)
    job_id = enqueue(conn, "scan", {"body": "city"})

    assert runner.run_once("normal") == job_id
    assert seen == [{"body": "city"}]
    row = get(conn, job_id)
    assert row["state"] == DONE
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
    assert row["last_error"] is None
    assert runner.run_once("normal") is None


def test_the_context_reports_its_own_job(db_path: Path, conn: sqlite3.Connection) -> None:
    seen: list[tuple[int, str, str]] = []
    registry = Registry()
    registry.register("scan", lambda ctx: seen.append((ctx.job_id, ctx.kind, ctx.lane)))
    job_id = enqueue(conn, "scan", None, lane="heavy")
    Runner(db_path, registry=registry).run_once("heavy")
    assert seen == [(job_id, "scan", "heavy")]


def test_a_handler_exception_fails_the_job_with_a_short_reason(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    def explode(ctx: JobContext) -> None:
        raise ValueError("the video has no captions and no audio track")

    registry = Registry()
    registry.register("capture", explode)
    job_id = enqueue(conn, "capture")
    Runner(db_path, registry=registry).run_once("normal")

    row = get(conn, job_id)
    assert row["state"] == FAILED
    assert row["last_error"] == "ValueError: the video has no captions and no audio track"
    assert "Traceback" not in row["last_error"]
    assert len(row["last_error"]) <= 300
    assert row["finished_at"] is not None


def test_a_job_with_no_handler_fails_with_a_plain_reason(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    job_id = enqueue(conn, "nothing_registered")
    Runner(db_path, registry=Registry()).run_once("normal")
    row = get(conn, job_id)
    assert row["state"] == FAILED
    assert row["last_error"] == "No handler is registered for job kind 'nothing_registered'."


def test_the_runner_drains_the_queue_in_lane_threads(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    done = threading.Event()
    count = {"n": 0}
    lock = threading.Lock()

    def handler(ctx: JobContext) -> None:
        with lock:
            count["n"] += 1
            if count["n"] == 3:
                done.set()

    registry = Registry()
    registry.register("scan", handler)
    runner = Runner(db_path, registry=registry)
    ids = [enqueue(conn, "scan", {"n": index}) for index in range(3)]
    runner.start()
    try:
        assert done.wait(timeout=5.0), "the lane workers did not run the jobs"
        assert wait_for(lambda: all(get(conn, job)["state"] == DONE for job in ids))
    finally:
        assert runner.stop(timeout=5.0) is True
    assert count["n"] == 3


def test_the_heavy_lane_never_runs_two_jobs_at_once(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    counter = Concurrency()
    registry = Registry()
    registry.register("draft", counter.handler, lane="heavy")
    runner = Runner(db_path, registry=registry)
    ids = [enqueue(conn, "draft", {"n": index}, lane="heavy") for index in range(4)]
    runner.start()
    try:
        assert wait_for(lambda: all(get(conn, job)["state"] == DONE for job in ids), timeout=10.0)
    finally:
        runner.stop(timeout=5.0)
    assert counter.peak == 1
    assert len(counter.seen) == 4


def test_transcribe_never_runs_two_jobs_at_once_across_lanes(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    counter = Concurrency()
    registry = Registry()
    registry.register("transcribe", counter.handler)
    settings = JobsSettings(lane_limits={"heavy": 1, "normal": 2})
    runner = Runner(db_path, registry=registry, settings=settings)
    ids = [
        enqueue(conn, "transcribe", {"n": 0}, lane="heavy"),
        enqueue(conn, "transcribe", {"n": 1}, lane="normal"),
        enqueue(conn, "transcribe", {"n": 2}, lane="normal"),
    ]
    runner.start()
    try:
        assert wait_for(lambda: all(get(conn, job)["state"] == DONE for job in ids), timeout=10.0)
    finally:
        runner.stop(timeout=5.0)
    assert counter.peak == 1
    assert len(counter.seen) == 3


def test_the_runner_keeps_the_heartbeat_fresh(db_path: Path, conn: sqlite3.Connection) -> None:
    release = threading.Event()
    started = threading.Event()

    def handler(ctx: JobContext) -> None:
        started.set()
        release.wait(timeout=5.0)

    registry = Registry()
    registry.register("capture", handler)
    settings = JobsSettings(heartbeat_interval=0.02, heartbeat_timeout=1.0, reclaim_interval=0.02)
    runner = Runner(db_path, registry=registry, settings=settings)
    job_id = enqueue(conn, "capture")
    runner.start()
    try:
        assert started.wait(timeout=5.0)
        claimed_at = get(conn, job_id)["claimed_at"]

        def beat_advanced() -> bool:
            row = get(conn, job_id)
            return row["heartbeat_at"] is not None and row["heartbeat_at"] > claimed_at

        assert wait_for(beat_advanced, timeout=3.0), "the runner never refreshed the heartbeat"
        assert get(conn, job_id)["state"] == "running"
    finally:
        release.set()
        runner.stop(timeout=5.0)
    assert wait_for(lambda: get(conn, job_id)["state"] == DONE)


def test_stop_stops_claiming_and_leaves_the_job_resumable(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    started = threading.Event()

    def handler(ctx: JobContext) -> None:
        ctx.save_checkpoint({"part": 1})
        started.set()
        while not ctx.should_stop():
            time.sleep(0.01)
        ctx.save_checkpoint({"part": 2, "stopped_early": True})
        ctx.interrupt("The runner was stopping.")

    registry = Registry()
    registry.register("capture", handler)
    runner = Runner(db_path, registry=registry)
    job_id = enqueue(conn, "capture")
    runner.start()
    assert started.wait(timeout=5.0)
    assert runner.stop(timeout=5.0) is True

    row = get(conn, job_id)
    assert row["state"] == QUEUED
    assert row["claim_token"] is None
    assert row["last_error"] == "The runner was stopping."
    assert row["checkpoint"] is not None
    assert runner.running_jobs() == ()

    resumed = claim(conn, "normal", "worker-9")
    assert resumed is not None
    assert resumed.job_id == job_id
    assert JobContext(
        conn=conn,
        job_id=resumed.job_id,
        kind=resumed.kind,
        payload=resumed.payload,
        lane=resumed.lane,
        claim_token=resumed.token,
    ).checkpoint() == {"part": 2, "stopped_early": True}


def test_a_runner_stops_cleanly_with_no_work(db_path: Path, conn: sqlite3.Connection) -> None:
    runner = Runner(db_path, registry=Registry())
    runner.start()
    assert runner.stop(timeout=5.0) is True
    assert runner.running_jobs() == ()


def test_a_job_paused_by_its_handler_keeps_the_reason(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    def blocked(ctx: JobContext) -> None:
        ctx.pause("The YouTube account is not signed in.")

    registry = Registry()
    registry.register("capture", blocked)
    job_id = enqueue(conn, "capture")
    Runner(db_path, registry=registry).run_once("normal")
    assert get(conn, job_id)["state"] == "paused"
    assert get(conn, job_id)["last_error"] == "The YouTube account is not signed in."


def test_a_handler_that_lost_its_claim_cannot_close_the_job(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    settings = JobsSettings(lane_limits={"heavy": 1, "normal": 9})

    def steal(ctx: JobContext) -> None:
        # Make this job look abandoned, then let another worker take it over.
        conn.execute(
            "UPDATE jobs SET heartbeat_at = '2000-01-01T00:00:00.000000Z' WHERE id = ?",
            (ctx.job_id,),
        )
        assert requeue_stale(conn) == [ctx.job_id]
        again = claim(conn, "normal", "worker-other", settings=settings)
        assert again is not None
        assert again.job_id == ctx.job_id
        assert again.token != ctx.claim_token

    registry = Registry()
    registry.register("scan", steal)
    job_id = enqueue(conn, "scan")
    Runner(db_path, registry=registry).run_once("normal")

    row = get(conn, job_id)
    assert row["state"] == "running"
    assert str(row["claim_token"]).startswith("worker-other")


def test_a_job_left_running_by_a_hard_stop_goes_back_on_the_timeout(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    """A process that dies hard leaves its job `running`; the timeout gives it back.

    A kill is not a shutdown: nothing of the killed process runs, so the job it
    was running keeps the `running` state and stops sending heartbeats, and
    nothing it owns will ever move it again. The heartbeat timeout is the only
    thing that does (spec 16.1). This test measures both halves of that: with
    the timeout still ahead the job is left exactly as the kill left it, and
    once the timeout is behind it the next runner claims the job and the handler
    is handed the checkpoint the dead run wrote.
    """
    gone = threading.Event()
    started = threading.Event()

    def the_run_that_dies(ctx: JobContext) -> None:
        ctx.save_checkpoint({"pages": 40, "of": 120})
        started.set()
        gone.wait(timeout=30.0)  # the process is killed here, and never wakes

    first_registry = Registry()
    first_registry.register("extract_pages", the_run_that_dies, lane="heavy")
    first = Runner(db_path, registry=first_registry)
    job_id = enqueue(conn, "extract_pages", {"record_id": 7}, lane="heavy")

    # `run_once` is the claim path without a lane loop or a heartbeat thread, so
    # the job it takes has exactly what a killed process leaves behind: a claim,
    # a checkpoint, and a heartbeat that stops where it is.
    thread = threading.Thread(target=lambda: first.run_once("heavy"), daemon=True)
    thread.start()
    assert started.wait(timeout=5.0), "the first runner never started the job"

    left = get(conn, job_id)
    assert left["state"] == RUNNING
    assert left["claim_token"] is not None
    assert left["checkpoint"] is not None

    # The heartbeat has just been written, so the default timeout of 300s leaves
    # the job alone: this is the job the live run of 2026-09-27 found, and it
    # would have stayed exactly there for five minutes.
    assert requeue_stale(conn) == []
    assert get(conn, job_id)["state"] == RUNNING

    resumed: list[object] = []

    def the_next_run(ctx: JobContext) -> None:
        resumed.append(ctx.checkpoint())

    second_registry = Registry()
    second_registry.register("extract_pages", the_next_run, lane="heavy")
    second = Runner(
        db_path,
        registry=second_registry,
        settings=JobsSettings(
            heartbeat_timeout=0.2,
            heartbeat_interval=0.05,
            reclaim_interval=0.02,
            poll_interval=0.02,
        ),
    )
    second.start()
    try:
        assert wait_for(lambda: get(conn, job_id)["state"] == DONE, timeout=15.0), (
            f"the job the killed run left behind was never claimed again: {dict(get(conn, job_id))}"
        )
    finally:
        assert second.stop(timeout=5.0) is True
        gone.set()
        thread.join(timeout=5.0)

    assert resumed == [{"pages": 40, "of": 120}], "the next run did not read the checkpoint"
    row = get(conn, job_id)
    assert row["attempts"] == 1, "the lost run was not counted"
    assert row["claim_token"] is None
    assert row["last_error"] is None


def test_two_runners_share_the_queue_without_double_work(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    seen: list[int] = []
    lock = threading.Lock()

    def handler(ctx: JobContext) -> None:
        with lock:
            seen.append(ctx.job_id)
        time.sleep(0.02)

    registry = Registry()
    registry.register("scan", handler)
    ids = [enqueue(conn, "scan", {"n": index}) for index in range(6)]
    first = Runner(db_path, registry=registry)
    second = Runner(db_path, registry=registry)
    first.start()
    second.start()
    try:
        assert wait_for(lambda: all(get(conn, job)["state"] == DONE for job in ids), timeout=10.0)
    finally:
        first.stop(timeout=5.0)
        second.stop(timeout=5.0)
    assert sorted(seen) == ids, "a job ran twice or not at all"
