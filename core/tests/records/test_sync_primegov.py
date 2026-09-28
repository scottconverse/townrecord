"""The sync job: what one window of a PrimeGov portal becomes (spec 7.2, 7.3, 9.5).

Every test here names its own city through a source row and a fake portal on a
test host. Nothing in this file, and nothing in the job, knows a real city's
URL: the origin comes off the source row and from nowhere else (rule D), which
``test_the_origin_comes_off_the_source_row`` checks by syncing two different
sources and looking at which host each one asked.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

import pytest

from townrecord.jobs import DONE, FAILED, ORIGIN_SCHEDULED, PAUSED, read_checkpoint
from townrecord.records.portal import (
    DEFAULT_WINDOW_AHEAD_DAYS,
    DEFAULT_WINDOW_DAYS,
    SyncRefused,
    sync_request,
)
from townrecord.repo import (
    agenda_items,
    all_sources,
    get_meeting,
    insert_video,
    meeting_by_portal_id,
    primary_video,
    records_of_meeting,
    videos_of_platform,
)

from .conftest import (
    ARCHIVED_2026,
    BASE_URL,
    SIGNED_MARKER,
    Area,
    FakePortal,
    Sync,
    days_from_today,
    document,
    meeting,
    needs,
    trimmed_2026,
)

#: The meeting the sync tests list, unless a test says otherwise.
A_MEETING = 4100

#: The video URL of that meeting.
A_VIDEO_URL = "https://www.youtube.com/watch?v=3qfQAkAAC9U"

#: How far ahead of the day of the run a test puts a meeting that has not been
#: held yet. The default window reaches 60 days forward, so three weeks is
#: inside it with room to spare and is not at its edge.
FUTURE_DAYS = 21


def one_meeting(portal: FakePortal, **entry: Any) -> int:
    """List exactly one meeting on the fake portal and return its portal id."""
    listed = meeting(A_MEETING, "2026-09-08T19:00:00", "City Council Regular Session", **entry)
    portal.meetings = [listed]
    return int(listed["id"])


def a_default_sync(sync: Sync, area: Area) -> int:
    """Queue the sync a window-less payload asks for: the schedule's own job.

    It is the payload spec 16.2 queues for every accepted portal source, and it
    names no window, so the job chooses one.
    """
    return sync.queue("sync_primegov", {"source_id": area.portal_id})


# -- Rule D: no city of the job's own -----------------------------------------


def test_the_origin_comes_off_the_source_row(area: Area, wired: FakePortal, sync: Sync) -> None:
    """Two sources, two origins: each sync asks its own portal and no other.

    Both fake sources share one transport, so the host of every request is the
    host of the source row the job was given, and nothing else: the sync of the
    second source asks the second origin, which is the whole of rule D.
    """
    one_meeting(wired, video_url=None)
    first = sync.queue_sync(area.portal_id)
    sync.lane("normal")
    first_hosts = set(wired.hosts())

    wired.reset_requests()
    second = sync.queue_sync(area.other_portal_id)
    sync.lane("normal")
    second_hosts = set(wired.hosts())

    assert first_hosts == {"portal.test.invalid"}, "the first sync asked its own portal only"
    assert second_hosts == {"second.test.invalid"}, "the second sync asked its own origin"
    assert sync.job(first)["state"] == DONE
    assert sync.job(second)["state"] == DONE


def test_a_sync_no_city_appears_in_the_requests(area: Area, wired: FakePortal, sync: Sync) -> None:
    """Nothing the job asked for names a city, because no row did (rule D)."""
    one_meeting(wired, video_url=A_VIDEO_URL)
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    asked = " ".join(str(request.url) for request in wired.requests).casefold()

    assert "longmont" not in asked, "no real city was asked for"
    assert BASE_URL.casefold() in asked, "the configured origin is what was asked for"


def test_a_source_of_another_type_is_refused_by_name(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A video channel is not a meeting portal, and the job says which it is."""
    job_id = sync.queue_sync(area.channel_id)
    sync.lane("normal")

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert "video_channel" in row["last_error"]
    assert "not a meeting portal" in row["last_error"]
    assert wired.requests == [], "a refused sync reads nothing"


def test_a_source_nobody_accepted_is_refused(area: Area, wired: FakePortal, sync: Sync) -> None:
    """A suggested source is not synced, and the reason says so."""
    suggested = area.source(
        type="meeting_portal", origin="https://suggested.test.invalid", status="suggested"
    )
    job_id = sync.queue_sync(suggested)
    sync.lane("normal")

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert "suggested" in row["last_error"]
    assert "accepted or broken" in row["last_error"]


# -- Meetings, their portal ids and their cancellation ------------------------


def test_the_portal_meeting_id_lands_in_its_own_column(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 9.2: the portal's id is a column, not a field of a JSON blob."""
    one_meeting(wired)
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    row = sync.conn.execute("SELECT * FROM meetings").fetchone()
    assert row["portal_meeting_id"] == A_MEETING
    assert row["portal_source_id"] == area.portal_id
    assert row["body_id"] == area.body_id
    assert row["type"] == "regular"
    assert row["title"] == "City Council Regular Session"

    stored = meeting_by_portal_id(sync.conn, area.portal_id, A_MEETING)
    assert stored is not None and stored.id == row["id"]


def test_a_notice_of_cancellation_marks_the_meeting(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 9.5: the clerk's notice is the signal, and the meeting is marked."""
    one_meeting(
        wired,
        documents=(document(9001, 900, 1, "Notice of Cancellation"),),
    )
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    found = meeting_by_portal_id(sync.conn, area.portal_id, A_MEETING)
    assert found is not None
    assert found.is_cancelled is True
    titles = {record.title for record in records_of_meeting(sync.conn, found.id)}
    assert "Notice of Cancellation" not in titles, (
        "a cancellation notice is not a record of the meeting"
    )


def test_a_title_that_says_cancelled_marks_the_meeting(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """The other signal spec 9.5 records: a portal that marks it in the title."""
    wired.meetings = [
        meeting(A_MEETING, "2026-09-08T19:00:00", "City Council Regular Session - CANCELLED")
    ]
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    found = meeting_by_portal_id(sync.conn, area.portal_id, A_MEETING)
    assert found is not None
    assert found.is_cancelled is True
    assert found.body_id == area.body_id, "the body is still read out of the title"


def test_a_title_that_says_postponed_marks_the_meeting_continued(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """The third signal a portal marks in the title: the sitting did not happen.

    "POSTPONED to March 23" says the Parks Board did not sit on the day it was
    listed for, and that is what ``meetings.is_continued`` records. The status
    tail is not part of the body's name, so the body is still read out of the
    title, and the row is not a cancellation.
    """
    wired.meetings = [
        meeting(A_MEETING, "2026-09-08T19:00:00", "Parks Board POSTPONED to March 23")
    ]
    job_id = sync.queue_sync(area.portal_id)
    sync.lane("normal")

    found = meeting_by_portal_id(sync.conn, area.portal_id, A_MEETING)
    assert found is not None
    assert found.is_continued is True
    assert found.is_cancelled is False, "a postponed sitting is not a cancelled one"
    assert found.body_id == area.other_body_id, "the body is still read out of the title"
    assert read_checkpoint(sync.conn, job_id)["meetings_continued"] == 1


def test_a_continuation_is_never_cleared_by_a_later_listing(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Like a cancellation, a continuation is a fact about the meeting (spec 9.5).

    The portal writes the status on the listing that carried it, and the next
    window's listing of the same meeting carries it too or does not. Either way
    the row keeps what was read.
    """
    wired.meetings = [
        meeting(A_MEETING, "2026-09-08T19:00:00", "Parks Board POSTPONED to March 23")
    ]
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    wired.meetings = [meeting(A_MEETING, "2026-09-08T19:00:00", "Parks Board")]
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    found = meeting_by_portal_id(sync.conn, area.portal_id, A_MEETING)
    assert found is not None
    assert found.is_continued is True


def test_a_meeting_that_names_no_known_body_is_reported(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A body the jurisdiction does not hold is a skip with a reason, not a row."""
    wired.meetings = [
        meeting(A_MEETING, "2026-09-08T19:00:00", "Planning Commission Regular Session")
    ]
    job_id = sync.queue_sync(area.portal_id)
    sync.lane("normal")

    assert sync.conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 0
    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["skipped_total"] == 1
    assert "Planning Commission" in checkpoint["skipped"][0]
    assert "no body of jurisdiction" in checkpoint["skipped"][0]


@needs(ARCHIVED_2026)
def test_the_day_the_live_sync_stored_one_of_three_meetings_stores_two(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """The live defect, over the listing the portal really gave for that day.

    The portal listed three meetings for 2026-09-08 and the sync stored one:
    the pre-session of the City Council was read as a body called "City Council
    Pre" and skipped with it. The Housing Authority is still skipped, and
    correctly, so the count is two rows and one recorded skip, not two and none.
    """
    wired.meetings = trimmed_2026((3709, 3710, 3797))
    job_id = sync.queue_sync(area.portal_id, from_date="2026-09-08", to_date="2026-09-09")
    sync.lane("normal")

    stored = {
        row["portal_meeting_id"]: (row["type"], row["title"])
        for row in sync.conn.execute("SELECT portal_meeting_id, type, title FROM meetings")
    }
    assert stored == {
        3709: ("regular", "City Council Regular Session"),
        3710: ("special", "City Council Pre-Session"),
    }
    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["meetings_created"] == 2
    assert checkpoint["skipped_total"] == 1
    assert "Longmont Housing Authority Advisory Board" in checkpoint["skipped"][0]


# -- Videos -------------------------------------------------------------------


def test_a_video_url_becomes_a_video_row(area: Area, wired: FakePortal, sync: Sync) -> None:
    """Spec 7.2 step 7: the listing's videoUrl is the video, with no title invented."""
    one_meeting(wired, video_url=A_VIDEO_URL)
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    rows = videos_of_platform(sync.conn, "3qfQAkAAC9U")
    assert len(rows) == 1
    video = rows[0]
    assert video.meeting_id is not None
    assert video.url == A_VIDEO_URL
    assert video.is_primary is True
    assert video.title is None, "a listing gives no title, and none is invented"
    assert str(area.portal_id) in (video.source_note or "")
    assert video.capture_state == "pending", "a listing is not a capture"

    found = get_meeting(sync.conn, video.meeting_id)
    assert found is not None and found.portal_meeting_id == A_MEETING


def test_a_video_a_watched_channel_holds_is_adopted_and_never_renamed(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 7.2 step 7: the channel's row is the video, and it keeps its title."""
    video_id = insert_video(
        sync.conn,
        source_id=area.channel_id,
        platform_video_id="3qfQAkAAC9U",
        title="City Council Regular Session, September 8 2026",
        duration_s=14407,
        capture_state="captions",
        readiness="finished",
    )
    one_meeting(wired, video_url=A_VIDEO_URL)
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    rows = videos_of_platform(sync.conn, "3qfQAkAAC9U")
    assert [row.id for row in rows] == [video_id], "no second row was made for one video"
    adopted = rows[0]
    assert adopted.title == "City Council Regular Session, September 8 2026"
    assert adopted.source_id == area.channel_id, "the row stays the channel's"
    assert adopted.duration_s == 14407
    assert adopted.capture_state == "captions", "the sync does not touch a capture's state"
    assert adopted.meeting_id is not None
    assert adopted.url == A_VIDEO_URL
    assert primary_video(sync.conn, adopted.meeting_id) is not None


def test_a_video_url_that_is_not_a_video_link_is_reported(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A link with no id this job can read is a skip with its reason."""
    one_meeting(wired, video_url="https://example.invalid/watch/whatever")
    job_id = sync.queue_sync(area.portal_id)
    sync.lane("normal")

    assert sync.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0
    checkpoint = read_checkpoint(sync.conn, job_id)
    assert "not a link this sync can read an id out of" in checkpoint["skipped"][0]


# -- Documents ----------------------------------------------------------------


def test_each_compiled_document_becomes_one_heavy_download_job(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 9.6: a packet is heavy, and one document is one job."""
    wired.meetings = [
        meeting(
            A_MEETING,
            "2026-09-08T19:00:00",
            "City Council Regular Session",
            documents=(
                document(18613, 16805, 1, "Agenda"),
                document(18658, 16807, 1, "Agenda Packet"),
                document(18657, 16805, 3, "HTML Agenda"),
                document(9001, 900, 1, "Notice of Cancellation"),
            ),
        )
    ]
    job_id = sync.queue_sync(area.portal_id)
    sync.lane("normal")

    downloads = sync.jobs_of_kind("download_record")
    assert [row["lane"] for row in downloads] == ["heavy", "heavy"]
    assert {row["state"] for row in downloads} == {"queued"}
    assert {row["origin"] for row in downloads} == {"manual"}, (
        "a sync a person queued makes a manual child"
    )
    assert [json.loads(row["payload"]) for row in downloads] == [
        {
            "source_id": area.portal_id,
            "meeting_id": 1,
            "document_id": 18613,
            "template_id": 16805,
            "compile_output_type": 1,
            "template_name": "Agenda",
        },
        {
            "source_id": area.portal_id,
            "meeting_id": 1,
            "document_id": 18658,
            "template_id": 16807,
            "compile_output_type": 1,
            "template_name": "Agenda Packet",
        },
    ], "one job per compiled document, in the order the portal listed them"

    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["documents_queued"] == 2
    assert checkpoint["html_agendas_read"] == 1
    assert any("Notice of Cancellation" in sentence for sentence in checkpoint["skipped"])


def test_a_scheduled_sync_queues_children_the_schedule_asked_for(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 16.1: who asked for the parent asked for the children too.

    The daily schedule queues this sync itself, and every job it makes is the
    schedule's work and not a person's. A child left ``manual`` would tell the
    user a download they never asked for was their own doing.
    """
    wired.meetings = [
        meeting(
            A_MEETING,
            "2026-09-08T19:00:00",
            "City Council Regular Session",
            documents=(document(18613, 16805, 1, "Agenda"),),
        )
    ]
    parent = sync.queue_sync(area.portal_id, origin=ORIGIN_SCHEDULED)
    sync.lane("normal")

    assert sync.job(parent)["origin"] == ORIGIN_SCHEDULED
    downloads = sync.jobs_of_kind("download_record")
    assert len(downloads) == 1
    assert downloads[0]["origin"] == ORIGIN_SCHEDULED, (
        "a child of a scheduled sync says the schedule asked for it"
    )


def test_the_html_agenda_is_read_here_and_its_items_are_stored(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 9.2: the HTML agenda is small, and its items are stored without times."""
    wired.meetings = [
        meeting(
            A_MEETING,
            "2026-09-08T19:00:00",
            "City Council Regular Session",
            video_url=A_VIDEO_URL,
            documents=(document(18657, 16805, 3, "HTML Agenda"),),
        )
    ]
    job_id = sync.queue_sync(area.portal_id)
    sync.lane("normal")

    found = meeting_by_portal_id(sync.conn, area.portal_id, A_MEETING)
    assert found is not None
    assert found.portal_html_template_id == 16805

    items = agenda_items(sync.conn, found.id)
    by_number = {item.number: item for item in items}
    assert by_number["1."].title == "MEETING CALLED TO ORDER"
    assert by_number["1."].start_ms is None, "the sync stores no time; alignment does that"
    assert by_number["1."].alignment_method == "none"
    assert by_number["9.B"].identifiers == {"O-2026-58": "ordinance"}

    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["agenda_items_written"] == len(items)
    assert checkpoint["alignments_queued"] == 1, "items and a video are what an align job needs"


def test_no_align_job_without_a_video(area: Area, wired: FakePortal, sync: Sync) -> None:
    """Items with nothing to align to queue no align job."""
    wired.meetings = [
        meeting(
            A_MEETING,
            "2026-09-08T19:00:00",
            "City Council Regular Session",
            documents=(document(18657, 16805, 3, "HTML Agenda"),),
        )
    ]
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    assert sync.jobs_of_kind("align_meeting") == []


def test_an_agenda_page_that_cannot_be_read_does_not_stop_the_window(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """One unreadable page is a skip with its reason; the rest of the window runs."""
    wired.portal_meeting_status = 500
    wired.meetings = [
        meeting(
            A_MEETING,
            "2026-09-08T19:00:00",
            "City Council Regular Session",
            documents=(document(18657, 16805, 3, "HTML Agenda"),),
        ),
        meeting(
            A_MEETING + 1,
            "2026-09-15T19:00:00",
            "City Council Regular Session",
            video_url=A_VIDEO_URL,
            documents=(document(18613, 16805, 1, "Agenda"),),
        ),
    ]
    job_id = sync.queue_sync(area.portal_id)
    sync.lane("normal")

    row = sync.job(job_id)
    assert row["state"] == DONE, "the window ran to its end"
    assert sync.conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] == 2
    assert len(sync.jobs_of_kind("download_record")) == 1
    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["html_agendas_read"] == 0
    assert any(
        "could not be read" in sentence and "answered with status 500" in sentence
        for sentence in checkpoint["skipped"]
    ), "the skip carries the page and the reason"


# -- Source health (spec 7.3) -------------------------------------------------


def test_three_failed_listings_make_the_source_broken_then_a_success_resets_it(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 7.3, the whole arc: three in a row, then one success."""
    wired.archived_status = 500
    job_ids = [sync.queue_sync(area.portal_id) for _ in range(3)]
    sync.lane("normal")

    for job_id in job_ids:
        row = sync.job(job_id)
        assert row["state"] == FAILED, "a listing that failed is a failed job"
        assert "AdapterHttpError" in row["last_error"]

    source = next(row for row in all_sources(sync.conn) if row.id == area.portal_id)
    assert source.consecutive_failures == 3
    assert source.status == "broken"
    assert source.last_error and "answered with status 500" in source.last_error
    assert "It is broken." in sync.job(job_ids[-1])["last_error"]

    wired.archived_status = 200
    one_meeting(wired)
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    restored = next(row for row in all_sources(sync.conn) if row.id == area.portal_id)
    assert restored.consecutive_failures == 0
    assert restored.status == "accepted", "a source that answers again is usable again"
    assert restored.last_error is None


def test_one_oversized_document_does_not_make_the_source_broken(
    area: Area, wired: FakePortal, sync: Sync, monkeypatch: Any
) -> None:
    """Spec 9.6: a document is refused by itself; the portal answered fine."""
    monkeypatch.setenv("TOWNRECORD_PDF_LIMIT_BYTES", "1024")
    wired.document_bytes = b"x" * 4096
    wired.meetings = [
        meeting(
            A_MEETING,
            "2026-09-08T19:00:00",
            "City Council Regular Session",
            documents=(document(18613, 16805, 1, "Agenda"),),
        )
    ]
    sync.queue_sync(area.portal_id)
    sync.all_lanes()

    source = next(row for row in all_sources(sync.conn) if row.id == area.portal_id)
    assert source.status == "accepted"
    assert source.consecutive_failures == 0


# -- Running the same window twice --------------------------------------------


def test_a_second_sync_of_the_same_window_writes_no_second_row(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A re-sync is the ordinary case, and it is idempotent."""
    wired.meetings = [
        meeting(
            A_MEETING,
            "2026-09-08T19:00:00",
            "City Council Regular Session",
            video_url=A_VIDEO_URL,
            documents=(
                document(18613, 16805, 1, "Agenda"),
                document(18657, 16805, 3, "HTML Agenda"),
            ),
        )
    ]
    sync.queue_sync(area.portal_id)
    sync.lane("normal")
    first = {
        "meetings": sync.conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0],
        "videos": sync.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
        "items": sync.conn.execute("SELECT COUNT(*) FROM agenda_items").fetchone()[0],
        "downloads": len(sync.jobs_of_kind("download_record")),
    }

    second_id = sync.queue_sync(area.portal_id)
    sync.lane("normal")
    second = {
        "meetings": sync.conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0],
        "videos": sync.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0],
        "items": sync.conn.execute("SELECT COUNT(*) FROM agenda_items").fetchone()[0],
        "downloads": len(sync.jobs_of_kind("download_record")),
    }

    assert first == second
    checkpoint = read_checkpoint(sync.conn, second_id)
    assert checkpoint["meetings_created"] == 0
    assert checkpoint["meetings_updated"] == 1
    assert checkpoint["documents_queued"] == 0
    assert checkpoint["documents_pending"] == 1, "the queued download already does that work"
    assert checkpoint["alignments_queued"] == 1, (
        "the align job of the first window ran and paused for want of a transcript, "
        "and a job that could not run is queued again (spec 7.1 step 7)"
    )


def test_the_signed_link_of_a_download_is_never_written(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 9.2: the download passes through the signed link and drops it."""
    wired.meetings = [
        meeting(
            A_MEETING,
            "2026-09-08T19:00:00",
            "City Council Regular Session",
            documents=(document(18613, 16805, 1, "Agenda"),),
        )
    ]
    sync.queue_sync(area.portal_id)
    sync.all_lanes()

    asked = [str(request.url) for request in wired.compiled_requests()]
    assert asked[0].startswith(BASE_URL), "the download starts at the portal"
    assert wired.signed_url(1) in asked, "and it passes through the signed storage link"
    assert _database_text(sync.conn).count(SIGNED_MARKER) == 0, "and it was not written anywhere"


def _database_text(conn: sqlite3.Connection) -> str:
    """Every text value of every table, for a search that has no column to miss."""
    pieces: list[str] = []
    tables = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 - a test's own tables
        pieces.extend(str(value) for row in rows for value in tuple(row))
    return "\n".join(pieces)


def test_a_window_that_ends_before_it_starts_is_refused(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A payload nobody can act on is refused with a sentence, not run backwards."""
    job_id = sync.queue_sync(area.portal_id, from_date="2026-09-30", to_date="2026-09-01")
    sync.lane("normal")

    row = sync.job(job_id)
    assert row["state"] == FAILED
    assert "before it starts" in row["last_error"]
    assert wired.requests == []


# -- The window a payload names, and the one it does not ----------------------


def test_the_default_window_stores_a_meeting_that_has_not_been_held_yet(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 9.2: the upcoming list is asked, and what it lists is kept.

    The meeting here is on the portal's upcoming list and in no archived year,
    and it starts three weeks after the day of the run. An unbound sync stores
    it only if the window the job chooses for itself reaches past today, and
    the row it writes is the row the next regular session of spec 9.4 is read
    from. Nothing about the meeting is a fault: it has no video, no minutes and
    no packet yet because it has not been held (rule 4).
    """
    wired.meetings = [meeting(A_MEETING, days_from_today(-1), "City Council Regular Session")]
    future = days_from_today(FUTURE_DAYS)
    wired.upcoming = [meeting(A_MEETING + 1, future, "City Council Regular Session")]

    job_id = a_default_sync(sync, area)
    sync.lane("normal")

    assert sync.job(job_id)["state"] == DONE
    stored = meeting_by_portal_id(sync.conn, area.portal_id, A_MEETING + 1)
    assert stored is not None, "the meeting the upcoming list carried is stored"
    assert stored.starts_at == future
    assert stored.body_id == area.body_id
    assert stored.type == "regular"
    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["meetings_created"] == 2
    assert checkpoint["skipped_total"] == 0, "an unheld meeting is not a fault"
    assert checkpoint["to_date"] >= (date.today() + timedelta(days=FUTURE_DAYS)).isoformat(), (
        "the window the job chose for itself reaches the day of the unheld meeting"
    )
    assert sync.conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0, (
        "a meeting with no video yet gets no video row and no complaint"
    )


def test_the_default_window_still_reaches_the_thirty_days_behind_today(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """The window a payload does not name still looks back 30 days (spec 7.1).

    The meeting is three weeks behind the day of the run: inside the window's
    start and outside a window that only looked forward.
    """
    wired.meetings = [meeting(A_MEETING, days_from_today(-21), "City Council Regular Session")]

    a_default_sync(sync, area)
    sync.lane("normal")

    assert meeting_by_portal_id(sync.conn, area.portal_id, A_MEETING) is not None


def test_a_window_a_caller_states_is_the_window_it_gets(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A payload that names both ends is obeyed, and today is its end here.

    The default window reaches into the future, and this is what stops that
    reaching being applied to a caller who said what it wanted: the upcoming
    meeting is fetched from the portal and dropped by the stated window, which
    is the behavior the caller asked for.
    """
    wired.meetings = [meeting(A_MEETING, "2026-09-08T19:00:00", "City Council Regular Session")]
    wired.upcoming = [meeting(A_MEETING + 1, "2026-09-29T19:00:00", "City Council Regular Session")]

    sync.queue_sync(area.portal_id, from_date="2026-09-01", to_date="2026-09-15")
    sync.lane("normal")

    assert meeting_by_portal_id(sync.conn, area.portal_id, A_MEETING) is not None
    assert meeting_by_portal_id(sync.conn, area.portal_id, A_MEETING + 1) is None, (
        "the stated window is the window: it ends on September 15"
    )
    assert wired.paths().count("/api/v2/PublicPortal/ListUpcomingMeetings") == 1, (
        "the upcoming list is still asked: the window is what drops the meeting"
    )


# -- The window the payload does and does not name (sync_request) -------------


def test_a_payload_that_names_no_window_gets_the_default_one() -> None:
    """The two ends of the default window, on a day the test fixes itself.

    The window the job chooses is what the tests above observe through a fake
    portal; this is the arithmetic on its own, with ``today`` passed in rather
    than read off the machine's clock.
    """
    asked = sync_request({"source_id": 7}, today=date(2026, 9, 27))

    assert asked.source_id == 7
    assert asked.date_from == date(2026, 9, 27) - timedelta(days=DEFAULT_WINDOW_DAYS)
    assert asked.date_to == date(2026, 9, 27) + timedelta(days=DEFAULT_WINDOW_AHEAD_DAYS)
    assert asked.date_from == date(2026, 8, 28), "the month behind the day of the run"
    assert asked.date_to == date(2026, 11, 26), "and the two months ahead of it"


def test_a_bare_source_id_is_the_same_payload_as_a_mapping_of_one() -> None:
    """A payload that is the id alone names no window either (spec 7.1 step 5)."""
    bare = sync_request(7, today=date(2026, 9, 27))
    mapped = sync_request({"source_id": 7}, today=date(2026, 9, 27))

    assert bare == mapped


def test_a_window_that_names_both_ends_is_obeyed_exactly() -> None:
    """Nothing is widened for a caller that said what it wanted."""
    asked = sync_request(
        {"source_id": 7, "from_date": "2026-09-01", "to_date": "2026-09-15"},
        today=date(2026, 9, 27),
    )

    assert asked.date_from == date(2026, 9, 1)
    assert asked.date_to == date(2026, 9, 15)


def test_a_window_that_names_only_its_start_still_ends_today() -> None:
    """Half a window is filled in, and today closes it, as it always did.

    Only the unbound case reaches forward: a caller that named a start said what
    it wanted at the end it cared about, and the end of the window is not made
    to reach past the day of the run behind its back.
    """
    asked = sync_request({"source_id": 7, "from_date": "2026-09-01"}, today=date(2026, 9, 27))

    assert asked.date_from == date(2026, 9, 1)
    assert asked.date_to == date(2026, 9, 27)


def test_a_window_that_ends_before_it_starts_is_refused_by_name() -> None:
    """The refusal the job test sees as a failed job, at the source of it."""
    with pytest.raises(SyncRefused):
        sync_request(
            {"source_id": 7, "from_date": "2026-09-30", "to_date": "2026-09-01"},
            today=date(2026, 9, 27),
        )
