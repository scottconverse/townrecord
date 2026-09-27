"""The align job's promise, kept by the real capture, through the real runner.

The align job pauses when its meeting's video has no transcript yet, and the
sentence it leaves says two things will make it run again: the meeting is synced
again, or a transcript of that video is stored (spec 16.3). The sync half is
held by :mod:`tests.records.test_request_alignment`. This file holds the other
half with nothing stood in for: the real sync lists the recorded meeting, the
real capture job stores the recorded caption track through the shared writer of
spec 8.5, and the real runner then runs the paused alignment to its end and
writes the items unit F measured.

The transcript is never written by hand here. That is the point: the asking is
one line inside the one function that stores a transcript, so a run that reaches
that function is the only thing that can prove the promise.
"""

from __future__ import annotations

from pathlib import Path

from townrecord.capture import JOB_KIND, register
from townrecord.jobs import DONE, PAUSED, QUEUED, read_checkpoint
from townrecord.repo import (
    agenda_items,
    get_meeting_alignment,
    meeting_by_portal_id,
    primary_video,
)

from ..capture.fakes import INFO_AUTO_CAPTIONS, FakeYtDlp
from .conftest import (
    AGENDA_16805,
    CAPTIONS_16805,
    MEASURED_COUNTS,
    MEASURED_OFFSET_S,
    MEETING_3709,
    VIDEO_3709,
    VIDEO_3709_DURATION_S,
    Area,
    FakePortal,
    Sync,
    needs,
    trimmed_2026,
)


@needs(AGENDA_16805, CAPTIONS_16805)
def test_a_real_capture_revives_the_paused_alignment(
    area: Area, wired: FakePortal, sync: Sync, storage_root: Path
) -> None:
    """Capture, ask, and align: the whole second half of the promise.

    The capture job is put on the queue on its own and run once, so the moment
    the asking happens is visible as a state of the align row: it was paused,
    and the transcript write put it back on the queue with the sentence that
    names the video and the meeting. Only then is the lane drained, which is
    what turns the waiting alignment into items.
    """
    wired.html_agenda = AGENDA_16805.read_bytes()
    wired.meetings = trimmed_2026()

    # -- the sync lists the meeting, whose alignment has nothing to read ------
    sync.queue_sync(area.portal_id)
    sync.lane("normal")

    meeting = meeting_by_portal_id(sync.conn, area.portal_id, MEETING_3709)
    assert meeting is not None, "the recorded meeting was listed and stored"
    video = primary_video(sync.conn, meeting.id)
    assert video is not None and video.platform_video_id == VIDEO_3709
    paused = [row for row in sync.jobs_of_kind("align_meeting") if row["state"] == PAUSED]
    assert len(paused) == 1, "the sync queued one alignment, and it paused"
    align_id = paused[0]["id"]
    assert "has no transcript yet" in paused[0]["last_error"]

    # The portal listing carries neither the length of the video nor its
    # status, so a capture run over this meeting needs both written down first.
    # This is the same two facts any other test of the capture job states; the
    # reading of status from the feed (spec 8.1) is not part of the portal sync.
    sync.conn.execute(
        "UPDATE videos SET duration_s = ?, readiness = 'finished' WHERE id = ?",
        (VIDEO_3709_DURATION_S, video.id),
    )

    # -- the capture stores the recorded caption track through the writer -----
    register(
        storage_root=storage_root,
        registry=sync.registry,
        runner=FakeYtDlp(
            platform_video_id=VIDEO_3709,
            caption=CAPTIONS_16805.read_bytes(),
            caption_ext="srv3",
            info={**INFO_AUTO_CAPTIONS, "id": VIDEO_3709, "duration": VIDEO_3709_DURATION_S},
        ),
        interpreter="python",
    )
    capture_id = sync.queue(JOB_KIND, video.id)

    assert sync.runner().run_once("normal") == capture_id, "one job ran: the capture"

    stored = sync.conn.execute(
        "SELECT * FROM transcripts WHERE video_id = ?", (video.id,)
    ).fetchall()
    assert len(stored) == 1 and stored[0]["origin"] == "auto_captions"
    row = sync.job(align_id)
    assert row["state"] == QUEUED, "the transcript write put the alignment back"
    assert row["last_error"] == (
        f"A transcript of video {video.id} of meeting {meeting.id} was stored, "
        "so the alignment runs now."
    )

    # -- and the runner runs it to its end ------------------------------------
    sync.lane("normal")

    row = sync.job(align_id)
    assert row["state"] == DONE
    checkpoint = read_checkpoint(sync.conn, align_id)
    assert checkpoint["counts_by_method"] == MEASURED_COUNTS
    assert checkpoint["offset_s"] == MEASURED_OFFSET_S
    alignment = get_meeting_alignment(sync.conn, meeting.id)
    assert alignment is not None
    assert alignment.video_id == video.id
    assert alignment.transcript_id == stored[0]["id"], "the alignment read the captured transcript"
    placed = [item for item in agenda_items(sync.conn, meeting.id) if item.start_ms is not None]
    assert len(placed) == (
        MEASURED_COUNTS["spoken_transitions"] + MEASURED_COUNTS["html_video_times"]
    )
