"""A real worker process killed hard, and the job it left behind (spec 16.1).

`test_runner.py` covers the same rule from inside one process:
`test_a_job_left_running_by_a_hard_stop_goes_back_on_the_timeout` starts a
handler in a thread that stops heartbeating and never comes back. That thread
is still alive, still holding its SQLite connection, and still part of the
process that owns the database. This file covers the hard stop the way it
really happens: the worker is an operating system process of its own, on a real
SQLite file, and it is killed with the hardest signal the system has
(`Popen.kill`, which is `SIGKILL` on POSIX and `TerminateProcess` on Windows).
Nothing of it runs afterwards, so nothing is released politely and no thread is
left to send one more heartbeat.

The proof is the four things the backlog note asks for, measured on real rows:
the killed run leaves the job `running`; the heartbeat stops where the kill
left it; the heartbeat timeout puts the job back in the queue with its
checkpoint and one more attempt; and a second real worker claims it and
finishes it from that checkpoint.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from townrecord.jobs import (
    DONE,
    RUNNING,
    enqueue,
    get,
    read_checkpoint,
)

#: The worker this test starts. A file next to this one, run as a script.
WORKER = Path(__file__).with_name("hard_stop_worker.py")

#: The job kind and lane the worker serves, spelled the same as in the worker.
KIND = "hb1_extract"
LANE = "heavy"

#: The checkpoint the blocking run saves. Spelled the same as in the worker.
CHECKPOINT = {"pages": 40, "of": 120}

#: How long the test waits for a real process to do something. Generous: CI
#: machines are slow and a process start is a process start.
PATIENCE = 30.0


def wait_for(predicate: Callable[[], bool], timeout: float = PATIENCE) -> bool:
    """Wait for something another process brings about."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def trace(note: str, **fields: object) -> None:
    """Print one line of evidence, with the wall clock that produced it.

    The report quotes these lines, so they carry real timestamps and real row
    values rather than a summary of them.
    """
    moment = datetime.now(UTC).strftime("%H:%M:%S.%f")
    tail = " ".join(f"{name}={value}" for name, value in fields.items())
    print(f"[HB1 {moment}] {note} {tail}".rstrip(), flush=True)


def spawn_worker(mode: str, db_path: Path, tmp_path: Path, log: Path) -> subprocess.Popen[str]:
    """Start one real worker process, with the short timings the proof needs."""
    env = os.environ.copy()
    env.update(
        {
            "HB1_DB": str(db_path),
            "HB1_MODE": mode,
            "HB1_STARTED": str(tmp_path / "started"),
            "HB1_CHECKPOINT_SEEN": str(tmp_path / "checkpoint.json"),
            # Short enough that the proof runs in seconds, and long enough that
            # a live worker beats several times before the kill.
            "TOWNRECORD_JOBS_HEARTBEAT_TIMEOUT": "1.0",
            "TOWNRECORD_JOBS_HEARTBEAT_INTERVAL": "0.05",
            "TOWNRECORD_JOBS_RECLAIM_INTERVAL": "0.05",
            "TOWNRECORD_JOBS_POLL_INTERVAL": "0.02",
        }
    )
    # The child inherits its own handle at spawn, so this one closes as soon as
    # the process is started. The log stays readable by path either way.
    with log.open("a", encoding="utf-8") as stream:
        return subprocess.Popen(
            [sys.executable, str(WORKER)],
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
        )


def stop_worker(process: subprocess.Popen[str]) -> None:
    """Stop one worker this test started, and only that one."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:  # pragma: no cover - a stuck child
            process.kill()
            process.wait(timeout=10.0)


def test_a_killed_worker_leaves_a_job_that_the_timeout_gives_back(
    db_path: Path, conn: sqlite3.Connection, tmp_path: Path
) -> None:
    """The hard stop, end to end, on real rows and real processes."""
    log = tmp_path / "workers.log"
    started = tmp_path / "started"
    seen = tmp_path / "checkpoint.json"
    job_id = enqueue(conn, KIND, {"record_id": 7}, lane=LANE)
    trace("enqueued", job=job_id, state=get(conn, job_id)["state"])

    first = spawn_worker("block", db_path, tmp_path, log)
    trace("started the first worker", pid=first.pid)
    try:
        assert wait_for(started.exists), (
            f"the first worker never got inside its handler:\n{log.read_text(encoding='utf-8')}"
        )
        # A live worker heartbeats. Without this half, a job that never beat at
        # all would look exactly like a job whose worker was killed.
        first_beat = get(conn, job_id)["heartbeat_at"]
        assert first_beat is not None, "a claimed job must carry a heartbeat"
        assert wait_for(lambda: get(conn, job_id)["heartbeat_at"] != first_beat), (
            "the live worker never refreshed the heartbeat"
        )
        before = get(conn, job_id)
        assert before["state"] == RUNNING
        assert before["claim_token"] is not None
        assert read_checkpoint(conn, job_id) == CHECKPOINT
        assert before["attempts"] == 0
        trace(
            "the job is running in that process",
            job=job_id,
            state=before["state"],
            attempts=before["attempts"],
            heartbeat=before["heartbeat_at"],
            checkpoint=before["checkpoint"],
        )

        # The hard stop. `Popen.kill` is SIGKILL on POSIX and TerminateProcess
        # on Windows: in both cases the process gets no say in it.
        first.kill()
        first.wait(timeout=PATIENCE)
        # Read the row again now that the process is reaped: from here on, any
        # beat in it could only come from a process that is already gone.
        at_kill = get(conn, job_id)
        trace(
            "killed the first worker",
            pid=first.pid,
            returncode=first.returncode,
            state=at_kill["state"],
            heartbeat=at_kill["heartbeat_at"],
        )

        # The heartbeat interval is 0.05s, so a beat would have landed by now if
        # anything of the dead process were still running.
        time.sleep(0.3)
        left = get(conn, job_id)
        trace(
            "what the kill left behind",
            job=job_id,
            state=left["state"],
            attempts=left["attempts"],
            heartbeat=left["heartbeat_at"],
            checkpoint=left["checkpoint"],
        )
        assert left["state"] == RUNNING, "the killed run did not leave the job running"
        assert left["heartbeat_at"] == at_kill["heartbeat_at"], "the dead worker kept beating"
        assert left["attempts"] == 0
        assert read_checkpoint(conn, job_id) == CHECKPOINT

        # A second real worker, on the same file. Nothing else moves the job:
        # only the heartbeat timeout can.
        second = spawn_worker("resume", db_path, tmp_path, log)
        trace("started the second worker", pid=second.pid)
        try:
            assert wait_for(lambda: get(conn, job_id)["state"] == DONE), (
                f"the job the kill left running was never claimed again: "
                f"{dict(get(conn, job_id))}\n{log.read_text(encoding='utf-8')}"
            )
        finally:
            stop_worker(second)
        trace("the second worker finished it", pid=second.pid)
    finally:
        stop_worker(first)

    assert json.loads(seen.read_text(encoding="utf-8")) == CHECKPOINT, (
        "the second run did not resume from the checkpoint the dead run wrote"
    )
    row = get(conn, job_id)
    trace(
        "the row after the second run",
        job=job_id,
        state=row["state"],
        attempts=row["attempts"],
        heartbeat=row["heartbeat_at"],
        checkpoint=row["checkpoint"],
        last_error=row["last_error"],
    )
    assert row["state"] == DONE
    assert row["attempts"] == 1, "the lost run was not counted exactly once"
    assert row["claim_token"] is None
    assert row["last_error"] is None
    assert first.returncode is not None, "the first worker was never reaped"
