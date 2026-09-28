"""What ``townrecord status`` reports (spec 16.2, 16.3).

Spec 16.3 refuses a health page that answers "OK" from a status code. Every
assertion here is about real content: a job count by lane, a source's failures,
the newest capture of a body, the version of a tool that is not installed, a
run that could not happen and the sentence that says why.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from datetime import time as clock_time
from pathlib import Path

import pytest

from townrecord import serving
from townrecord.config import Settings
from townrecord.jobs import QUEUED, RUNNING, Registry, claim, enqueue
from townrecord.records import DOWNLOAD_RECORD, SYNC_PRIMEGOV, register_jobs
from townrecord.repo import (
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_source,
    insert_video,
    record_enqueued_run,
    record_paused_run,
    record_source_failure,
)
from townrecord.schedule import NO_TEST_VIDEO_REASON, source_subject, tool_subject
from townrecord.status import collect, render

#: A moment the tests hold the clock at, so no line of a report carries the
#: wall clock's own time and a test can ask what is not in the text.
STARTED = datetime(2026, 9, 27, 23, 21, 0, tzinfo=UTC)


@pytest.fixture
def settings(db_path: Path, tmp_path: Path) -> Settings:
    return Settings(
        db_path=db_path,
        storage_root=tmp_path / "storage",
        runtime_root=tmp_path / "runtime",
        time_zone="America/Denver",
        version="0.1.0-test",
    )


def build_area(conn: sqlite3.Connection) -> tuple[int, int]:
    """One city, one body, a portal and a channel, and two listed videos."""
    city = insert_jurisdiction(conn, type="city", name="Longmont")
    body = insert_body(conn, jurisdiction_id=city, name="City Council")
    portal = insert_source(
        conn,
        jurisdiction_id=city,
        body_id=body,
        type="meeting_portal",
        origin="https://portal.test.invalid",
        suggested_by="discovery",
        reason="The city's meeting page links to it.",
        status="accepted",
    )
    channel = insert_source(
        conn,
        jurisdiction_id=city,
        type="video_channel",
        origin="https://videos.test.invalid/channel/UC-test",
        suggested_by="discovery",
        reason="The portal lists this channel for its recordings.",
        status="accepted",
    )
    meeting = insert_meeting(
        conn,
        body_id=body,
        title="City Council Regular Session",
        starts_at="2026-09-08T19:00:00-06:00",
    )
    insert_video(
        conn,
        source_id=channel,
        meeting_id=meeting,
        platform_video_id="v-0001",
        title="City Council Regular Session",
        published_at="2026-09-08T19:00:00-06:00",
        capture_state="failed",
        is_primary=True,
    )
    insert_video(
        conn,
        source_id=channel,
        meeting_id=meeting,
        platform_video_id="v-0002",
        title="City Council Regular Session",
        published_at="2026-09-15T19:00:00-06:00",
        capture_state="pending",
    )
    return portal, body


def test_it_says_when_there_is_no_database_yet(settings: Settings) -> None:
    """The first run of the service has nothing to report, and says so."""
    report = collect(settings)
    assert report.db_present is False
    assert report.notes
    assert "townrecord serve" in report.notes[0]

    text = render(report)
    assert "not created yet" in text
    assert "no time zone is set" not in text  # Denver is set, and it is shown
    assert re.search(r"\bOK\b", text) is None
    # Asking what is running is a read: it leaves no folder a service never made.
    assert not settings.runtime_root.exists()


def test_it_reports_jobs_by_state_and_lane(settings: Settings, conn: sqlite3.Connection) -> None:
    registry = Registry()
    register_jobs(registry)
    enqueue(conn, SYNC_PRIMEGOV, {"source_id": 1}, registry=registry)
    heavy = enqueue(conn, DOWNLOAD_RECORD, {"record_id": 1}, registry=registry)
    taken = claim(conn, "heavy", "heavy-1")
    assert taken is not None and taken.job_id == heavy

    report = collect(settings)
    states = {job.state: job for job in report.jobs}
    assert states[QUEUED].count == 1
    assert states[QUEUED].lanes == (("normal", 1),)
    assert states[RUNNING].count == 1
    assert states[RUNNING].lanes == (("heavy", 1),)
    assert report.oldest_queued_at is not None

    text = render(report)
    assert "queued      1  (normal 1)" in text
    assert "running     1  (heavy 1)" in text
    assert "heavy 1, normal 2 at a time" in text


def test_it_reports_a_job_kind_nothing_can_run(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    enqueue(conn, "ghost_kind", {"body": "city"})
    report = collect(settings)
    assert report.unhandled_kinds == ("ghost_kind",)
    assert "no handler is registered for: ghost_kind" in render(report)


def test_it_reports_sources_and_their_failures(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    portal, _body = build_area(conn)
    assert record_source_failure(conn, portal, "the portal answered 500") == 1

    report = collect(settings)
    by_id = {source.id: source for source in report.sources}
    assert set(by_id) == {portal, portal + 1}
    assert by_id[portal].consecutive_failures == 1
    assert by_id[portal].last_error == "the portal answered 500"
    assert by_id[portal].last_checked_at is not None
    assert by_id[portal].origin == "https://portal.test.invalid"

    text = render(report)
    assert "https://portal.test.invalid" in text
    assert "1 failure(s) in a row" in text
    assert "the portal answered 500" in text


def test_it_reports_the_newest_capture_of_each_body(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    _portal, body = build_area(conn)

    report = collect(settings)
    assert len(report.captures) == 1
    capture = report.captures[0]
    assert capture.body_id == body
    assert capture.body == "City Council"
    assert capture.videos == 2
    assert capture.pending == 1
    assert capture.failed == 1
    assert capture.last_video == "v-0002"
    assert capture.last_state == "pending"
    assert capture.last_at == "2026-09-15T19:00:00-06:00"

    text = render(report)
    assert "City Council: 2 video(s), 1 pending, 1 without captions" in text
    assert "newest v-0002 (pending) at 2026-09-15T19:00:00-06:00" in text


def test_it_reports_the_runs_the_schedule_made_and_could_not_make(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    portal, _body = build_area(conn)
    job = enqueue(conn, SYNC_PRIMEGOV, {"source_id": portal})
    record_enqueued_run(
        conn,
        task=SYNC_PRIMEGOV,
        subject=source_subject(portal),
        local_date="2026-09-27",
        time_zone="America/Denver",
        job_id=job,
    )
    record_paused_run(
        conn,
        task="runtime_update",
        subject=tool_subject("yt-dlp"),
        local_date="2026-09-27",
        time_zone="America/Denver",
        reason=NO_TEST_VIDEO_REASON,
    )

    report = collect(settings)
    assert {run.subject for run in report.runs} == {
        source_subject(portal),
        tool_subject("yt-dlp"),
    }
    assert report.paused[0].reason == NO_TEST_VIDEO_REASON
    assert report.paused[0].job_id is None

    text = render(report)
    assert "2026-09-27" in text
    assert "[America/Denver]" in text
    assert NO_TEST_VIDEO_REASON in text
    assert "enqueued (job " in text

    # A run that could not happen is listed with its reason where it falls among
    # the newest runs, and gets its own heading once it falls outside them.
    record_enqueued_run(
        conn,
        task=SYNC_PRIMEGOV,
        subject=source_subject(portal),
        local_date="2026-09-28",
        time_zone="America/Denver",
        job_id=enqueue(conn, SYNC_PRIMEGOV, {"source_id": portal}),
    )
    scrolled = collect(settings, limit=1)
    assert [run.local_date for run in scrolled.runs] == ["2026-09-28"]
    assert [run.local_date for run in scrolled.paused] == ["2026-09-27"]
    scrolled_text = render(scrolled)
    assert "Runs that could not happen" in scrolled_text
    assert re.search(r"runtime_update +tool:yt-dlp +paused\b", scrolled_text)


def test_it_reports_the_tools_and_never_calls_them_usable_by_guess(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    report = collect(settings)
    tools = {tool.tool: tool for tool in report.tools}
    assert set(tools) == {"yt-dlp", "textflowkit"}
    for tool in report.tools:
        assert tool.usable is False
        assert tool.reason

    text = render(report)
    assert "not installed --" in text


def test_the_report_is_never_just_ok(settings: Settings, conn: sqlite3.Connection) -> None:
    """Spec 16.3: the page shows real content, and "OK" is not content."""
    build_area(conn)
    text = render(collect(settings))

    assert re.search(r"\bOK\b", text) is None
    assert "TownRecord 0.1.0-test" in text
    assert "a token is required on every route" in text
    assert "America/Denver, daily at 06:00 local time" in text
    for heading in ("Jobs (", "Sources", "Captures (", "Tools", "Scheduled runs ("):
        assert heading in text


# -- a running service, and the shell that asks about it ----------------------


def test_it_reports_the_running_service_not_the_caller_s_settings(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    """The live run of 2026-09-27: served on 8791 at 17:21, reported as 8190 at 06:00.

    Each number the report gave was true of the shell that asked and false of the
    service that was running, which is spec 16.3's own failure: content that
    reads as a fact and is not one.
    """
    build_area(conn)
    claim = serving.record(
        settings.runtime_root,
        host="127.0.0.1",
        port=8791,
        daily_time=clock_time(17, 21),
        time_zone="America/New_York",
        db_path=str(settings.db_path),
        pid=4321,
        clock=lambda: STARTED,
    )
    try:
        report = collect(settings, clock=lambda: STARTED)
        text = render(report)
    finally:
        serving.clear(claim)

    assert "running as process 4321" in text
    assert "http://127.0.0.1:8791" in text
    assert "America/New_York, daily at 17:21 local time" in text
    assert "127.0.0.1:8190" not in text
    assert "daily at 06:00" not in text

    assert report.service is not None
    assert report.service.pid == 4321
    assert report.port == 8791
    assert report.time_zone == "America/New_York"
    assert report.daily_time == "17:21"


def test_it_says_plainly_when_no_service_is_running(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    """What the report shows then is this shell's configuration, and it says so.

    The service file is left behind on purpose: a service that was killed leaves
    one, and it must not read as a service that is still there.
    """
    build_area(conn)
    settings.runtime_root.mkdir(parents=True, exist_ok=True)
    (settings.runtime_root / serving.SERVICE_FILE_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "pid": 4321,
                "host": "127.0.0.1",
                "port": 8791,
                "daily_time": "17:21",
                "time_zone": "America/New_York",
                "db_path": str(settings.db_path),
                "started_at": "2026-09-27T23:21:00.000000Z",
            }
        ),
        encoding="utf-8",
    )

    report = collect(settings, clock=lambda: STARTED)
    text = render(report)

    assert "no service is running" in text
    assert "configuration" in text
    assert "8791" not in text
    assert "17:21" not in text
    assert "America/Denver, daily at 06:00 local time" in text

    assert report.service is None
