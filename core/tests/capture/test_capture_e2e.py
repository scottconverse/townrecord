"""The capture job through the real runner, with a fake yt-dlp (spec 16.1).

Nothing here calls the handler directly: the job is enqueued, the real
:class:`~townrecord.jobs.Runner` claims it, and the state the caller then reads
is the state the runner wrote.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from townrecord.capture import JOB_KIND, register
from townrecord.jobs import DONE, QUEUED, Registry, Runner, enqueue, get
from townrecord.repo import set_capture_state

from .fakes import FakeClock, FakeYtDlp


def test_the_capture_runs_through_the_real_runner(
    db_path: Path, conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """One job, one fake yt-dlp, and the whole thing wired end to end."""
    registry = Registry()
    fake = FakeYtDlp()
    register(storage_root=storage_root, registry=registry, runner=fake, interpreter="python")
    runner = Runner(db_path, registry=registry)
    job_id = enqueue(conn, JOB_KIND, video)

    assert runner.run_once("normal") == job_id

    row = get(conn, job_id)
    assert row["state"] == DONE
    assert row["last_error"] is None
    assert row["finished_at"] is not None
    assert len(fake.calls) == 1
    assert fake.argv[2] == "yt_dlp"
    assert fake.argv[3] == "--skip-download"
    transcript = conn.execute("SELECT * FROM transcripts WHERE video_id = ?", (video,)).fetchone()
    assert transcript is not None
    assert transcript["origin"] == "auto_captions"
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM segments WHERE transcript_id = ?", (transcript["id"],)
        ).fetchone()["n"]
        == 10
    )
    assert (
        conn.execute("SELECT capture_state FROM videos WHERE id = ?", (video,)).fetchone()[
            "capture_state"
        ]
        == "captions"
    )


def test_a_deferred_job_is_not_claimed_again_until_its_time(
    db_path: Path, conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """The waiting of spec 8.2, seen from the runner: queued, and not picked up."""
    clock = FakeClock()
    registry = Registry()
    fake = FakeYtDlp()
    register(
        storage_root=storage_root,
        registry=registry,
        runner=fake,
        interpreter="python",
    )
    runner = Runner(db_path, registry=registry, clock=clock)
    conn.execute("UPDATE videos SET readiness = 'upcoming' WHERE id = ?", (video,))
    job_id = enqueue(conn, JOB_KIND, video)

    assert runner.run_once("normal") == job_id

    row = get(conn, job_id)
    assert row["state"] == QUEUED
    assert row["last_error"] == "upcoming, will retry"
    assert row["run_after"] is not None
    assert runner.run_once("normal") is None
    assert fake.calls == []
    assert (
        conn.execute("SELECT capture_state FROM videos WHERE id = ?", (video,)).fetchone()[
            "capture_state"
        ]
        == "skipped"
    )


def test_a_rate_limited_job_is_queued_again_by_the_runner(
    db_path: Path, conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """A 429 pauses nothing and fails nothing: the job waits and comes back."""
    from .fakes import fake_429

    clock = FakeClock()
    registry = Registry()
    fake = fake_429()
    register(storage_root=storage_root, registry=registry, runner=fake, interpreter="python")
    runner = Runner(db_path, registry=registry, clock=clock)
    set_capture_state(conn, video, "pending")
    job_id = enqueue(conn, JOB_KIND, video)

    assert runner.run_once("normal") == job_id

    row = get(conn, job_id)
    assert row["state"] == QUEUED
    assert row["last_error"] == "rate limited by YouTube (http error 429), will retry later"
    assert row["run_after"] == "2026-09-27T12:15:00.000000Z"
    assert len(fake.calls) == 1
    assert "--skip-download" in fake.argv
