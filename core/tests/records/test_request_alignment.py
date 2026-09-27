"""Asking for the alignment of a meeting whose job paused (spec 10.2, 16.3).

The align job pauses when its video has no transcript, and a paused job is not a
job that waits: nothing in the queue will pick it up again. Spec 16.3 asks every
message a job leaves to be true, so something has to do what the message says,
and two things do, through one function: the sync that lists the meeting again,
and the work that stores the transcript.

These tests hold the asking to its promise. The job is not stood in for: it runs
on the real queue, through the runner of the records registry, and the items it
writes are read back out of the database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from townrecord.artifacts import store
from townrecord.captions import parse_srv3
from townrecord.jobs import (
    DONE,
    PAUSED,
    QUEUED,
    JobNotPaused,
    enqueue,
    get,
    read_checkpoint,
    requeue_paused,
)
from townrecord.records import request_alignment
from townrecord.records.portal import AlignRequest
from townrecord.repo import (
    agenda_items,
    get_meeting_alignment,
    insert_segment,
    insert_transcript,
    insert_video,
    upsert_agenda_item,
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

#: The portal's own ids for the fixture meeting, as the align job tests use them.
A_MEETING = 3709
AGENDA_TEMPLATE_ID = 16805

#: The window the sync jobs here run over.
A_WINDOW = {"from_date": "2026-09-08", "to_date": "2026-09-09"}


def a_meeting(sync: Sync, area: Area) -> int:
    """Store the meeting the sync would have listed, and return its row id."""
    meeting_id, _ = upsert_meeting(
        sync.conn,
        body_id=area.body_id,
        starts_at="2026-09-08T19:00:00",
        title="City Council Regular Session",
        portal_source_id=area.portal_id,
        portal_meeting_id=A_MEETING,
        portal_html_template_id=AGENDA_TEMPLATE_ID,
    )
    return meeting_id


def a_video(sync: Sync, area: Area, meeting_id: int) -> int:
    """Store the meeting's primary video and return its id."""
    return insert_video(
        sync.conn,
        source_id=area.portal_id,
        platform_video_id=VIDEO_3709,
        meeting_id=meeting_id,
        url=f"https://www.youtube.com/watch?v={VIDEO_3709}",
        is_primary=True,
        duration_s=VIDEO_3709_DURATION_S,
    )


def a_listing() -> dict[str, Any]:
    """The portal's own entry for that meeting, as a later sync would list it."""
    return {
        "id": A_MEETING,
        "dateTime": "2026-09-08T19:00:00",
        "title": "City Council Regular Session",
        "videoUrl": f"https://www.youtube.com/watch?v={VIDEO_3709}",
        "documentList": [],
    }


def a_paused_alignment(sync: Sync, area: Area, meeting_id: int) -> int:
    """Queue the alignment of a meeting that has nothing to align it with yet."""
    job_id = sync.queue("align_meeting", AlignRequest(meeting_id, area.portal_id).as_payload())
    sync.lane("normal")
    row = sync.job(job_id)
    assert row["state"] == PAUSED, "the fixture meeting has no transcript, so it pauses"
    return job_id


def an_alignable_meeting(sync: Sync, area: Area) -> tuple[int, int]:
    """A meeting with a video, an item and a paused alignment, and their ids."""
    meeting_id = a_meeting(sync, area)
    a_video(sync, area, meeting_id)
    upsert_agenda_item(sync.conn, meeting_id=meeting_id, number="1", title="Call to Order")
    return meeting_id, a_paused_alignment(sync, area, meeting_id)


def the_transcript(sync: Sync, storage_root: Path, video_id: int) -> int:
    """Store the recorded caption track of the video, its lines and all."""
    artifact = store(sync.conn, storage_root, "transcript", CAPTIONS_16805.read_bytes(), "srv3")
    transcript_id = insert_transcript(
        sync.conn, video_id=video_id, artifact_id=artifact.id, origin="auto_captions"
    )
    for line in parse_srv3(CAPTIONS_16805.read_bytes()):
        insert_segment(
            sync.conn,
            transcript_id=transcript_id,
            start_ms=line.start_ms,
            end_ms=line.end_ms,
            text=line.text,
        )
    return transcript_id


def the_waiting_jobs(sync: Sync, kind: str) -> int:
    """How many jobs of one kind are queued or running."""
    return sync.conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE kind = ? AND state IN ('queued', 'running')", (kind,)
    ).fetchone()[0]


def sync_the_window(sync: Sync, area: Area) -> tuple[int, list[int]]:
    """Queue one sync of the fixture window, drain the lane, and report the runs.

    The lane runs until it has nothing left, so a job the sync puts back on the
    queue runs inside this call. The ids it ran are returned, which is how the
    tests below tell a job that came back from one that stayed paused.
    """
    job_id = sync.queue("sync_primegov", {"source_id": area.portal_id, **A_WINDOW})
    return job_id, sync.lane("normal")


# -- The promise the message makes ---------------------------------------------


@needs(AGENDA_16805, CAPTIONS_16805)
def test_a_paused_alignment_that_is_asked_for_runs_through_the_runner(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The defect: the pause promised a re-run that nothing performed.

    The transcript arrives from the video's own capture work, which asks for the
    alignment after storing it. From there the job has to run on its own and
    write the items, with the counts unit F measured on these same bytes.
    """
    wired.html_agenda = AGENDA_16805.read_bytes()
    meeting_id = a_meeting(sync, area)
    video_id = a_video(sync, area, meeting_id)
    job_id = a_paused_alignment(sync, area, meeting_id)

    the_transcript(sync, storage_root, video_id)
    assert request_alignment(sync.conn, meeting_id) == job_id, "the paused job is the one used"
    assert sync.job(job_id)["state"] == QUEUED

    sync.lane("normal")

    assert sync.job(job_id)["state"] == DONE
    checkpoint = read_checkpoint(sync.conn, job_id)
    assert checkpoint["meeting_id"] == meeting_id
    assert checkpoint["counts_by_method"] == MEASURED_COUNTS
    assert checkpoint["offset_s"] == MEASURED_OFFSET_S
    alignment = get_meeting_alignment(sync.conn, meeting_id)
    assert alignment is not None
    assert alignment.video_id == video_id
    placed = [item for item in agenda_items(sync.conn, meeting_id) if item.start_ms is not None]
    assert len(placed) == (
        MEASURED_COUNTS["spoken_transitions"] + MEASURED_COUNTS["html_video_times"]
    )


def test_the_paused_message_says_what_will_make_it_run(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 16.3: the reason names both things this code really does."""
    meeting_id = a_meeting(sync, area)
    video_id = a_video(sync, area, meeting_id)
    job_id = a_paused_alignment(sync, area, meeting_id)

    reason = sync.job(job_id)["last_error"]
    assert "has no transcript yet" in reason
    assert str(video_id) in reason
    assert "when a transcript of that video is stored" in reason
    assert f"or when meeting {meeting_id} is synced again" in reason


@needs(CAPTIONS_16805)
def test_the_transcript_writer_is_what_makes_it_run(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The caller lane 2 has: the asking sits where the transcript write does.

    Nothing here reads the capture lane; what is held is that the asking alone
    is enough to put the paused job back on the queue, exactly once.
    """
    meeting_id = a_meeting(sync, area)
    video_id = a_video(sync, area, meeting_id)
    job_id = a_paused_alignment(sync, area, meeting_id)

    the_transcript(sync, storage_root, video_id)
    asked = request_alignment(sync.conn, meeting_id)

    assert asked == job_id
    assert sync.job(job_id)["state"] == QUEUED
    assert the_waiting_jobs(sync, "align_meeting") == 1


# -- Asking once, and only once ------------------------------------------------


def test_asking_twice_leaves_one_job_on_the_queue(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A second ask does nothing: one meeting has one alignment waiting."""
    meeting_id, job_id = an_alignable_meeting(sync, area)

    assert request_alignment(sync.conn, meeting_id) == job_id
    assert request_alignment(sync.conn, meeting_id) is None, "the job is already waiting"
    assert the_waiting_jobs(sync, "align_meeting") == 1
    assert len(sync.jobs_of_kind("align_meeting")) == 1


def test_a_meeting_that_never_had_an_align_job_is_given_one(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A transcript for a meeting no sync ever queued is still aligned."""
    meeting_id = a_meeting(sync, area)

    job_id = request_alignment(sync.conn, meeting_id)

    assert job_id is not None
    row = sync.job(job_id)
    assert (row["kind"], row["state"], row["lane"]) == ("align_meeting", QUEUED, "normal")
    assert json.loads(row["payload"]) == AlignRequest(meeting_id, area.portal_id).as_payload()


def test_a_meeting_nobody_stored_is_not_queued(sync: Sync) -> None:
    """Nothing is queued for a meeting that is not there."""
    assert request_alignment(sync.conn, 999999) is None
    assert sync.jobs_of_kind("align_meeting") == []


def test_a_job_that_is_already_running_is_not_asked_for_again(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A run in progress is doing the work; a second ask adds nothing."""
    meeting_id = a_meeting(sync, area)
    a_video(sync, area, meeting_id)
    job_id = sync.queue("align_meeting", AlignRequest(meeting_id, area.portal_id).as_payload())
    sync.conn.execute("UPDATE jobs SET state = 'running' WHERE id = ?", (job_id,))

    assert request_alignment(sync.conn, meeting_id) is None
    assert sync.job(job_id)["state"] == "running"


# -- The other half: the sync of the meeting -----------------------------------


def test_the_sync_of_the_meeting_revives_its_paused_alignment(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """Spec 7.1 step 7: the sync is the second thing that makes the job run.

    The job that comes back is the one that paused, not a second job beside it,
    and it comes back far enough to be claimed and run again. A paused job is
    never claimed, so the run is what proves the sync revived this one. With no
    transcript stored yet it pauses again, which is the sentence it left being
    true a second time.
    """
    wired.meetings = [a_listing()]
    meeting_id, job_id = an_alignable_meeting(sync, area)
    assert sync.job(job_id)["state"] == PAUSED
    reason = sync.job(job_id)["last_error"]

    sync_id, ran = sync_the_window(sync, area)

    assert sync.job(sync_id)["state"] == DONE
    assert read_checkpoint(sync.conn, sync_id)["alignments_queued"] == 1
    assert ran.count(job_id) == 1, "the revived job was claimed and run, once"
    row = get(sync.conn, job_id)
    assert row is not None
    assert len(sync.jobs_of_kind("align_meeting")) == 1, "the paused job came back, alone"
    assert json.loads(row["payload"])["meeting_id"] == meeting_id, "and it names the same meeting"
    assert (row["state"], row["last_error"]) == (PAUSED, reason), "it paused for the same reason"


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_sync_runs_a_revived_alignment_all_the_way(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """The whole promise: a sync alone turns a paused job into aligned items.

    Nothing here stores a transcript after the pause: the asking is the sync
    listing the meeting again, which is the other half of the sentence the job
    left. The items it writes are the counts unit F measured.
    """
    wired.meetings = [a_listing()]
    wired.html_agenda = AGENDA_16805.read_bytes()
    meeting_id = a_meeting(sync, area)
    video_id = a_video(sync, area, meeting_id)
    upsert_agenda_item(sync.conn, meeting_id=meeting_id, number="1", title="Call to Order")
    job_id = a_paused_alignment(sync, area, meeting_id)
    the_transcript(sync, storage_root, video_id)
    assert sync.job(job_id)["state"] == PAUSED

    sync_id, ran = sync_the_window(sync, area)

    assert sync.job(sync_id)["state"] == DONE
    assert ran.count(job_id) == 1
    assert sync.job(job_id)["state"] == DONE, "the sync alone made the alignment run"
    assert read_checkpoint(sync.conn, job_id)["counts_by_method"] == MEASURED_COUNTS
    placed = [item for item in agenda_items(sync.conn, meeting_id) if item.start_ms is not None]
    assert len(placed) == (
        MEASURED_COUNTS["spoken_transitions"] + MEASURED_COUNTS["html_video_times"]
    )


def test_the_sync_counts_a_revived_alignment_as_queued(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """The checkpoint of the window says an alignment was queued again."""
    wired.meetings = [a_listing()]
    an_alignable_meeting(sync, area)

    first, _ = sync_the_window(sync, area)
    assert read_checkpoint(sync.conn, first)["alignments_queued"] == 1
    assert the_waiting_jobs(sync, "align_meeting") == 0, "and the lane then ran it"


def test_a_sync_of_a_meeting_whose_alignment_already_waits_queues_no_more(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """When the alignment is already waiting, a sync leaves it alone.

    The sync is run on its own, once, with the alignment already on the queue
    behind it: that is the state a transcript stored between two windows leaves.
    """
    wired.meetings = [a_listing()]
    meeting_id = a_meeting(sync, area)
    a_video(sync, area, meeting_id)
    upsert_agenda_item(sync.conn, meeting_id=meeting_id, number="1", title="Call to Order")
    sync_id = sync.queue("sync_primegov", {"source_id": area.portal_id, **A_WINDOW})
    waiting = request_alignment(sync.conn, meeting_id)
    assert waiting is not None and sync.job(waiting)["state"] == QUEUED

    assert sync.runner().run_once("normal") == sync_id

    assert read_checkpoint(sync.conn, sync_id)["alignments_queued"] == 0
    assert sync.job(waiting)["state"] == QUEUED, "it was not touched"
    assert len(sync.jobs_of_kind("align_meeting")) == 1


# -- The state transition itself ------------------------------------------------


def test_the_queue_refuses_to_revive_a_job_that_is_not_paused(sync: Sync) -> None:
    """One state transition, held to its own state."""
    job_id = enqueue(sync.conn, "align_meeting", {"meeting_id": 1})
    assert sync.job(job_id)["state"] == QUEUED

    with pytest.raises(JobNotPaused):
        requeue_paused(sync.conn, job_id, reason="a test asked for it")

    assert sync.job(job_id)["state"] == QUEUED, "the refused call changed nothing"


def test_a_paused_job_that_comes_back_keeps_its_attempts(
    area: Area, wired: FakePortal, sync: Sync
) -> None:
    """A job that never ran was not recovered, so `attempts` does not move."""
    meeting_id, job_id = an_alignable_meeting(sync, area)
    assert sync.job(job_id)["attempts"] == 0

    request_alignment(sync.conn, meeting_id)

    row = sync.job(job_id)
    assert (row["state"], row["attempts"]) == (QUEUED, 0)
    assert row["last_error"], "the reason it came back is kept until the next run claims it"
