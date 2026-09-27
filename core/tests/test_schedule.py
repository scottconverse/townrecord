"""The daily schedule (spec 16.2, 8.9).

One ``sync_primegov`` per accepted portal source per local day, the daily
yt-dlp check, a paused run with a plain reason when a run cannot happen, and a
clock the tests move across the daylight saving change of 2026-11-01 in
America/Denver. Nothing here reaches the network: the schedule enqueues, and
these tests read the queue.
"""

from __future__ import annotations

import sqlite3
import time as wall_clock
from collections.abc import Callable
from datetime import datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from townrecord.jobs import ORIGIN_MANUAL, ORIGIN_SCHEDULED, QUEUED, Registry, enqueue
from townrecord.records import register_jobs
from townrecord.repo import (
    insert_body,
    insert_jurisdiction,
    insert_source,
)
from townrecord.schedule import (
    NO_ORIGIN_REASON,
    NO_TEST_VIDEO_REASON,
    NO_ZONE_REASON,
    TASK_RUNTIME_UPDATE,
    TASK_SYNC,
    Scheduler,
    source_subject,
    tool_subject,
)

DENVER = "America/Denver"

#: The local time of the day's runs, in the zone above.
DAILY_TIME = clock_time(6, 0)

#: 2026-11-01 is the day the clocks go back in Denver: 02:00 MDT becomes 01:00
#: MST, so the local day is 25 hours long.
BEFORE_THE_CHANGE = "2026-10-31T13:00:00+00:00"  # 07:00 MDT on the 31st
UTC_IS_TOMORROW = "2026-11-01T02:00:00+00:00"  # still 20:00 MDT on the 31st
AFTER_THE_CHANGE_EARLY = "2026-11-01T12:30:00+00:00"  # 05:30 MST, before 06:00
AFTER_THE_CHANGE = "2026-11-01T13:30:00+00:00"  # 06:30 MST on the 1st

#: A test video, so the daily update check is not the thing under test here.
TEST_VIDEO = "https://videos.test.invalid/watch/known-video"


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, moment: str) -> None:
        self.moment = datetime.fromisoformat(moment)

    def __call__(self) -> datetime:
        return self.moment

    def set(self, moment: str) -> None:
        self.moment = datetime.fromisoformat(moment)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(BEFORE_THE_CHANGE)


def local_offset(moment: str) -> timedelta:
    """The offset of the Denver zone at one moment, so the change is visible."""
    return datetime.fromisoformat(moment).astimezone(ZoneInfo(DENVER)).utcoffset()


def add_portal(
    conn: sqlite3.Connection,
    *,
    name: str = "Longmont",
    origin: str = "https://portal.test.invalid",
    status: str = "accepted",
) -> int:
    """Store one meeting portal source and return its id."""
    jurisdiction = insert_jurisdiction(conn, type="city", name=name)
    body = insert_body(conn, jurisdiction_id=jurisdiction, name="City Council")
    return insert_source(
        conn,
        jurisdiction_id=jurisdiction,
        body_id=body,
        type="meeting_portal",
        origin=origin,
        suggested_by="discovery",
        reason="The city's meeting page links to it.",
        status=status,
    )


def build(
    db_path: Path, clock: FakeClock, *, time_zone: str = DENVER, test_video_url: str = TEST_VIDEO
) -> Scheduler:
    """A scheduler whose handlers come from the real registry of job kinds."""
    registry = Registry()
    register_jobs(registry)
    return Scheduler(
        db_path=db_path,
        registry=registry,
        time_zone=time_zone,
        daily_time=DAILY_TIME,
        test_video_url=test_video_url,
        clock=clock,
        interval_s=0.05,
    )


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = wall_clock.monotonic() + timeout
    while wall_clock.monotonic() < deadline:
        if predicate():
            return True
        wall_clock.sleep(0.01)
    return predicate()


def sync_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM jobs WHERE kind = ? ORDER BY id", (TASK_SYNC,)).fetchall()


def update_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM jobs WHERE kind = ? ORDER BY id", (TASK_RUNTIME_UPDATE,)
    ).fetchall()


def test_one_sync_per_source_per_local_day_across_a_dst_change(
    db_path: Path, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    source = add_portal(conn)
    scheduler = build(db_path, clock)

    # The 2026-11-01 change is real: the same zone is six hours behind UTC
    # before it and seven hours behind after it.
    assert local_offset(BEFORE_THE_CHANGE) == timedelta(hours=-6)
    assert local_offset(AFTER_THE_CHANGE) == timedelta(hours=-7)

    first = scheduler.tick()
    assert len(sync_jobs(conn)) == 1
    assert sync_jobs(conn)[0]["origin"] == ORIGIN_SCHEDULED
    run = conn.execute(
        "SELECT * FROM schedule_runs WHERE task = ? AND subject = ?",
        (TASK_SYNC, source_subject(source)),
    ).fetchone()
    assert run["local_date"] == "2026-10-31"
    assert run["time_zone"] == DENVER
    assert run["job_id"] in first

    # The same local day, ten ticks later, is still one run.
    assert scheduler.tick() == ()
    assert len(sync_jobs(conn)) == 1

    # This moment is already the next day in UTC and still the 31st in Denver,
    # and that Denver day has had its run.
    clock.set(UTC_IS_TOMORROW)
    assert scheduler.tick() == ()
    assert len(sync_jobs(conn)) == 1

    # 05:30 on the 1st is before the chosen time, so the new day is not owed.
    clock.set(AFTER_THE_CHANGE_EARLY)
    assert scheduler.tick() == ()
    assert len(sync_jobs(conn)) == 1

    # 06:30 the same morning: the 1st is owed, and it is a new run.
    clock.set(AFTER_THE_CHANGE)
    assert len(scheduler.tick()) > 0
    jobs = sync_jobs(conn)
    assert len(jobs) == 2
    days = [
        row["local_date"]
        for row in conn.execute(
            "SELECT local_date FROM schedule_runs WHERE task = ? AND subject = ? ORDER BY id",
            (TASK_SYNC, source_subject(source)),
        ).fetchall()
    ]
    assert days == ["2026-10-31", "2026-11-01"]


def test_two_sources_get_one_sync_each_per_local_day(
    db_path: Path, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    first = add_portal(conn, name="Longmont")
    second = add_portal(conn, name="Boulder", origin="https://portal2.test.invalid")
    scheduler = build(db_path, clock)

    scheduler.tick()
    jobs = sync_jobs(conn)
    assert len(jobs) == 2
    assert [job["state"] for job in jobs] == [QUEUED, QUEUED]
    subjects = {
        row["subject"]
        for row in conn.execute("SELECT subject FROM schedule_runs WHERE task = ?", (TASK_SYNC,))
    }
    assert subjects == {source_subject(first), source_subject(second)}


def test_the_daily_update_check_runs_once_with_a_test_video(
    db_path: Path, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    scheduler = build(db_path, clock)
    scheduler.tick()
    jobs = update_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["origin"] == ORIGIN_SCHEDULED
    row = conn.execute(
        "SELECT * FROM schedule_runs WHERE task = ?", (TASK_RUNTIME_UPDATE,)
    ).fetchone()
    assert row["subject"] == tool_subject("yt-dlp")
    assert row["state"] == "enqueued"

    scheduler.tick()
    assert len(update_jobs(conn)) == 1


def test_a_scheduled_run_and_a_manual_one_are_recorded_separately(
    db_path: Path, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    source = add_portal(conn)
    manual = enqueue(conn, TASK_SYNC, {"source_id": source})
    scheduler = build(db_path, clock)
    scheduler.tick()

    rows = {row["id"]: row for row in sync_jobs(conn)}
    assert rows[manual]["origin"] == ORIGIN_MANUAL
    scheduled = [row for job_id, row in rows.items() if job_id != manual]
    assert len(scheduled) == 1
    assert scheduled[0]["origin"] == ORIGIN_SCHEDULED

    recorded = conn.execute(
        "SELECT task, subject, local_date, job_id FROM schedule_runs WHERE task = ?", (TASK_SYNC,)
    ).fetchall()
    assert len(recorded) == 1
    assert recorded[0]["subject"] == source_subject(source)
    assert recorded[0]["job_id"] == scheduled[0]["id"]


def test_a_source_with_no_origin_pauses_with_its_reason(
    db_path: Path, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    nowhere = add_portal(conn, name="Nowhere", origin=" ", status="accepted")
    scheduler = build(db_path, clock)

    scheduler.tick()
    assert sync_jobs(conn) == []
    row = conn.execute(
        "SELECT * FROM schedule_runs WHERE task = ? AND subject = ?",
        (TASK_SYNC, source_subject(nowhere)),
    ).fetchone()
    assert row["state"] == "paused"
    assert row["job_id"] is None
    assert row["reason"] == NO_ORIGIN_REASON.format(source_id=nowhere)
    assert "TOWNRECORD" not in row["reason"]

    # A paused run is that day's record: the same refusal is not written twice.
    scheduler.tick()
    again = conn.execute(
        "SELECT COUNT(*) AS n FROM schedule_runs WHERE state = 'paused' AND task = ?", (TASK_SYNC,)
    ).fetchone()
    assert again["n"] == 1


def test_a_source_the_user_has_not_accepted_is_not_a_run(
    db_path: Path, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    """A suggested or rejected source is owed no run, so none is recorded.

    That is not a silent skip: no run was owed for it, and ``townrecord status``
    lists the source with the status that says so.
    """
    add_portal(conn, name="Suggested City", status="suggested")
    add_portal(conn, name="Turned Down", status="rejected", origin="https://no.test.invalid")
    scheduler = build(db_path, clock)

    scheduler.tick()
    assert sync_jobs(conn) == []
    runs = conn.execute("SELECT * FROM schedule_runs WHERE task = ?", (TASK_SYNC,)).fetchall()
    assert runs == []


def test_the_update_check_says_which_setting_is_missing(
    db_path: Path, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    scheduler = build(db_path, clock, test_video_url="")
    scheduler.tick()

    assert update_jobs(conn) == []
    row = conn.execute(
        "SELECT * FROM schedule_runs WHERE task = ?", (TASK_RUNTIME_UPDATE,)
    ).fetchone()
    assert row["state"] == "paused"
    assert row["reason"] == NO_TEST_VIDEO_REASON
    assert "TOWNRECORD_TEST_VIDEO" in row["reason"]


def test_no_time_zone_pauses_the_day_with_the_setting_to_change(
    db_path: Path, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    source = add_portal(conn)
    scheduler = build(db_path, clock, time_zone="")

    scheduler.tick()
    assert sync_jobs(conn) == []
    row = conn.execute(
        "SELECT * FROM schedule_runs WHERE task = ? AND subject = ?",
        (TASK_SYNC, source_subject(source)),
    ).fetchone()
    assert row["state"] == "paused"
    assert row["reason"] == NO_ZONE_REASON
    assert "TOWNRECORD_TIME_ZONE" in row["reason"]


def test_the_schedule_runs_on_a_thread_and_stops(
    db_path: Path, conn: sqlite3.Connection, clock: FakeClock
) -> None:
    add_portal(conn)
    scheduler = build(db_path, clock)
    scheduler.start()
    try:
        assert wait_for(lambda: len(sync_jobs(conn)) == 1), "the schedule never enqueued the day"
    finally:
        assert scheduler.stop(timeout=5.0) is True

    count = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE kind = ?", (TASK_SYNC,)).fetchone()
    assert count["n"] == 1
