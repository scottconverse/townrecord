"""The align job: an agenda item's place in the video (spec 10.2, 7.1 step 7).

Alignment needs three things to be in place: the meeting's agenda, its video
and that video's transcript. Each of them arrives from a different job, so most
of what this job does is say plainly which one is missing and pause. The last
test here runs it on the real September 8, 2026 meeting, where unit F measured
22 spoken transitions, 6 published times and 12 items with neither.
"""

from __future__ import annotations

from pathlib import Path

from townrecord.artifacts import store
from townrecord.captions import parse_srv3
from townrecord.jobs import DONE, PAUSED, read_checkpoint
from townrecord.records.portal import AlignRequest
from townrecord.repo import (
    agenda_items,
    get_meeting_alignment,
    insert_segment,
    insert_transcript,
    insert_video,
    upsert_meeting,
)

from .conftest import (
    AGENDA_16805,
    CAPTIONS_16805,
    MEASURED_COUNTS,
    MEASURED_OFFSET_S,
    VIDEO_3709,
    VIDEO_3709_DURATION_S,
    Area,
    FakePortal,
    Sync,
    needs,
)

#: The portal's own ids for the fixture meeting.
A_MEETING = 3709
AGENDA_TEMPLATE_ID = 16805


def a_meeting(
    sync: Sync,
    area: Area,
    *,
    template_id: int | None = AGENDA_TEMPLATE_ID,
    from_portal: bool = True,
) -> int:
    """Store one meeting the sync would have listed, and return its row id."""
    meeting_id, _ = upsert_meeting(
        sync.conn,
        body_id=area.body_id,
        starts_at="2026-09-08T19:00:00",
        title="City Council Regular Session",
        portal_source_id=area.portal_id if from_portal else None,
        portal_meeting_id=A_MEETING if from_portal else None,
        portal_html_template_id=template_id,
    )
    return meeting_id


def a_video(sync: Sync, area: Area, meeting_id: int, *, duration_s: int | None = None) -> int:
    """Store the meeting's primary video and return its id."""
    return insert_video(
        sync.conn,
        source_id=area.portal_id,
        platform_video_id=VIDEO_3709,
        meeting_id=meeting_id,
        url=f"https://www.youtube.com/watch?v={VIDEO_3709}",
        is_primary=True,
        duration_s=duration_s,
    )


def a_transcript(sync: Sync, storage_root: Path, video_id: int, text: bytes) -> int:
    """Store one auto caption transcript of a video, with no lines yet."""
    artifact = store(sync.conn, storage_root, "transcript", text, "srv3")
    return insert_transcript(
        sync.conn, video_id=video_id, artifact_id=artifact.id, origin="auto_captions"
    )


def its_lines(sync: Sync, transcript_id: int, text: bytes) -> None:
    """Store the lines of one transcript, parsed from the recorded track."""
    for segment in parse_srv3(text):
        insert_segment(
            sync.conn,
            transcript_id=transcript_id,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            text=segment.text,
        )


def align(sync: Sync, meeting_id: int, source_id: int | None) -> int:
    """Queue one align job and run the lane it belongs to."""
    job_id = sync.queue("align_meeting", AlignRequest(meeting_id, source_id).as_payload())
    sync.lane("normal")
    return job_id


# -- The gaps that pause it ---------------------------------------------------


def test_a_meeting_that_is_not_there_pauses(area: Area, wired: FakePortal, sync: Sync) -> None:
    """A job whose meeting is gone says so."""
    job_id = align(sync, 999, area.portal_id)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert row["last_error"] == "There is no meeting 999 to align."


def test_a_meeting_with_no_html_agenda_template_pauses(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """No published template means no published times to align against."""
    meeting_id = a_meeting(sync, area, template_id=None)
    job_id = align(sync, meeting_id, area.portal_id)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert "carries no HTML agenda template id" in row["last_error"]
    assert wired.requests == []


def test_a_meeting_no_portal_listed_pauses(area: Area, wired: FakePortal, sync: Sync) -> None:
    """Without a portal to ask, the agenda cannot be read a second time."""
    meeting_id = a_meeting(sync, area, from_portal=False)
    job_id = align(sync, meeting_id, None)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert "was not listed by a meeting portal source" in row["last_error"]


def test_a_meeting_with_no_video_pauses(area: Area, wired: FakePortal, sync: Sync) -> None:
    """Items with nothing to align to pause, rather than write empty times."""
    meeting_id = a_meeting(sync, area)
    job_id = align(sync, meeting_id, area.portal_id)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert "has no primary video" in row["last_error"]


def test_a_video_with_no_transcript_pauses_and_says_it_runs_again(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """The ordinary case: the captions have not been fetched yet (spec 10.2)."""
    meeting_id = a_meeting(sync, area)
    a_video(sync, area, meeting_id, duration_s=VIDEO_3709_DURATION_S)
    job_id = align(sync, meeting_id, area.portal_id)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert "has no transcript yet" in row["last_error"]
    assert "runs again when the transcript is there" in row["last_error"]
    assert wired.requests == [], "a paused job does not read the agenda it cannot use"


def test_a_transcript_with_no_lines_pauses(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """An empty transcript is not the same as no transcript, and says which."""
    meeting_id = a_meeting(sync, area)
    video_id = a_video(sync, area, meeting_id, duration_s=VIDEO_3709_DURATION_S)
    a_transcript(sync, storage_root, video_id, b"a transcript with no lines")
    job_id = align(sync, meeting_id, area.portal_id)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert "has no lines" in row["last_error"]


def test_an_agenda_page_that_cannot_be_read_pauses_with_the_reason(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """A job that cannot read its own input did not fail at anything."""
    wired.portal_meeting_status = 500
    meeting_id = a_meeting(sync, area)
    video_id = a_video(sync, area, meeting_id, duration_s=VIDEO_3709_DURATION_S)
    transcript_id = a_transcript(sync, storage_root, video_id, CAPTIONS_16805.read_bytes())
    its_lines(sync, transcript_id, CAPTIONS_16805.read_bytes())
    job_id = align(sync, meeting_id, area.portal_id)

    row = sync.job(job_id)
    assert row["state"] == PAUSED
    assert "could not be read" in row["last_error"]


# -- The real meeting, end to end ---------------------------------------------


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_real_meeting_aligns_the_way_unit_f_measured(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The recorded September 8, 2026 meeting, with unit F's counts.

    Unit F measured this meeting's agenda against this video's captions on the
    same recorded bytes: 22 items placed by a spoken transition, 6 by a time the
    clerk published, 12 by neither. The job is held to those numbers here, and
    to the offset of 466 seconds the anchors agree on.
    """
    wired.html_agenda = AGENDA_16805.read_bytes()
    meeting_id = a_meeting(sync, area)
    video_id = a_video(sync, area, meeting_id, duration_s=VIDEO_3709_DURATION_S)
    transcript_id = a_transcript(sync, storage_root, video_id, CAPTIONS_16805.read_bytes())
    its_lines(sync, transcript_id, CAPTIONS_16805.read_bytes())

    job_id = align(sync, meeting_id, area.portal_id)

    assert sync.job(job_id)["state"] == DONE
    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["counts_by_method"] == MEASURED_COUNTS
    assert checkpoint["agenda_items"] == sum(MEASURED_COUNTS.values())
    assert checkpoint["offset_s"] == MEASURED_OFFSET_S
    assert checkpoint["offset_accepted"] is True

    stored = get_meeting_alignment(sync.conn, meeting_id)
    assert stored is not None
    assert stored.video_id == video_id
    assert stored.transcript_id == transcript_id
    assert stored.agenda_item_count == sum(MEASURED_COUNTS.values())
    assert (stored.spoken_transitions, stored.html_video_times, stored.no_alignment) == (
        MEASURED_COUNTS["spoken_transitions"],
        MEASURED_COUNTS["html_video_times"],
        MEASURED_COUNTS["none"],
    )
    assert stored.offset_s == MEASURED_OFFSET_S
    assert stored.offset_accepted is True
    assert stored.anchors, "an accepted offset rests on anchors, and they are stored"
    assert stored.anchors_agreeing > 0
    assert all("item_number" in anchor and "agreeing" in anchor for anchor in stored.anchors)

    # The items themselves carry the boundaries, and they add up to the counts.
    items = agenda_items(sync.conn, meeting_id)
    by_method: dict[str, int] = {}
    for item in items:
        by_method[item.alignment_method] = by_method.get(item.alignment_method, 0) + 1
    assert by_method == MEASURED_COUNTS
    placed = [item for item in items if item.start_ms is not None]
    placed_count = MEASURED_COUNTS["spoken_transitions"] + MEASURED_COUNTS["html_video_times"]
    assert len(placed) == placed_count
    assert all(item.end_ms is not None and item.end_ms > item.start_ms for item in placed)
    # An item with no boundary says what was missing for it; an item that was
    # placed carries the evidence the aligner matched instead of a reason, so
    # only the unplaced ones are held to a sentence here.
    unplaced = [item for item in items if item.alignment_method == "none"]
    assert len(unplaced) == MEASURED_COUNTS["none"]
    assert all(item.alignment_reason for item in unplaced), "an unplaced item says why"


@needs(AGENDA_16805, CAPTIONS_16805)
def test_aligning_the_same_meeting_twice_writes_the_same_rows(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The job is safe to re-run: a later sync queues it again (spec 7.1 step 7)."""
    wired.html_agenda = AGENDA_16805.read_bytes()
    meeting_id = a_meeting(sync, area)
    video_id = a_video(sync, area, meeting_id, duration_s=VIDEO_3709_DURATION_S)
    transcript_id = a_transcript(sync, storage_root, video_id, CAPTIONS_16805.read_bytes())
    its_lines(sync, transcript_id, CAPTIONS_16805.read_bytes())
    align(sync, meeting_id, area.portal_id)
    first = _item_times(sync, meeting_id)
    first_row = get_meeting_alignment(sync.conn, meeting_id)
    assert first_row is not None

    second_job = align(sync, meeting_id, area.portal_id)

    assert sync.job(second_job)["state"] == DONE
    assert _item_times(sync, meeting_id) == first
    assert sync.conn.execute("SELECT COUNT(*) FROM agenda_items").fetchone()[0] == len(first)
    assert sync.conn.execute("SELECT COUNT(*) FROM meeting_alignments").fetchone()[0] == 1
    again = get_meeting_alignment(sync.conn, meeting_id)
    assert again is not None
    assert again.offset_s == first_row.offset_s
    assert again.anchors == first_row.anchors
    assert again.updated_at >= first_row.updated_at


def _item_times(sync: Sync, meeting_id: int) -> dict[str, tuple[int | None, int | None, str]]:
    """Each item's boundary and method, by number, for a comparison of runs."""
    return {
        item.number: (item.start_ms, item.end_ms, item.alignment_method)
        for item in agenda_items(sync.conn, meeting_id)
    }
