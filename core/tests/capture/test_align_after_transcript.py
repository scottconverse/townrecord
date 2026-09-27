"""The other half of the align job's promise (spec 16.3, 8.3, 8.4, 8.5).

An align job that pauses says two things will make it run again: the meeting is
synced again, or a transcript of that video is stored. The first half is the
sync's (lane 1). This file holds the second half: the work that stores a
transcript has to ask for the alignment, and nothing else does.

The asking is not a step of its own. Spec 8.5 allows no second code path for a
transcript, so one function writes every transcript and one function asks, and
each of the three origins has to reach that one writer: captions, the sister
channel of spec 8.4.1, and the local transcription of spec 8.5. Each origin is
run here through its own caller -- the real capture job, the real hand-off, the
real transcription job -- because a test that called the writer directly would
prove the writer works and nothing about who reaches it.

Only a video that is *the* primary video of its meeting asks. A meeting is
aligned against one video, so a transcript of some other video of the meeting
changes nothing about it. Both halves of that rule are held below.

Nothing here reaches YouTube or runs a model: the runners are the fakes of
:mod:`tests.capture.fakes`.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from townrecord import artifacts, repo
from townrecord.captions.parse import Segment
from townrecord.capture import CaptionCapture, TranscribeAudio, insert_transcript_with_segments
from townrecord.capture.transcribe import JOB_KIND as TRANSCRIBE_KIND
from townrecord.capture.transcribe import LANE as TRANSCRIBE_LANE
from townrecord.capture.transcribe import payload_for
from townrecord.jobs import PAUSED, QUEUED, JobContext, JobPaused, claim, enqueue, get
from townrecord.records.align import align_meeting
from townrecord.records.portal import ALIGN_MEETING, AlignRequest
from townrecord.records.requests import TRANSCRIPT_STORED
from townrecord.repo import insert_source, insert_video
from townrecord.stt.audio import AudioTrigger

from .fakes import PLATFORM_VIDEO_ID, FakeClock, FakeTextFlowKit, FakeYtDlp, claimed_job

#: The source type a meeting portal has (spec 6.2). The align job refuses a
#: meeting whose source is not one, so the fixture meeting needs one.
MEETING_PORTAL = "meeting_portal"

#: The HTML agenda template id a sync would have written on the meeting. The
#: align job pauses without it, and the pause this file is about is later.
TEMPLATE_ID = 16805

#: The origin of a transcript taken from the same meeting on another channel.
SISTER_ORIGIN = "sister_channel"

#: The lines of the sister channel's transcript, as its own capture left them.
SISTER_SEGMENTS = (
    Segment(start_ms=0, end_ms=1500, text="Sister line one."),
    Segment(start_ms=1500, end_ms=3000, text="Sister line two."),
)

#: The audio the fake download writes. Nothing here decodes it.
AUDIO_BYTES = b"\x1aE\xdf\xa3 not really opus, but it hashes like it\x00\x01"

#: The sentence the capture job hands off with, as this file needs one.
HANDED_OFF = "the captions were unusable"


@dataclass
class TwoPrograms:
    """One runner for the two children of a transcription of spec 8.5.

    A real machine runs yt-dlp and TextFlowKit out of two runtimes, and this
    dispatches the way a machine does: by the program the argv names.
    """

    ytdlp: FakeYtDlp
    textflowkit: FakeTextFlowKit

    def __call__(self, argv: Sequence[str], *, timeout_s: float, env: Mapping[str, str]) -> Any:
        command = [str(part) for part in argv]
        if Path(command[0]).stem.lower().startswith("textflowkit"):
            return self.textflowkit(command, timeout_s=timeout_s, env=env)
        return self.ytdlp(command, timeout_s=timeout_s, env=env)


@pytest.fixture
def portal_source(conn: sqlite3.Connection, city: int, body: int) -> int:
    """The meeting portal the meeting was listed by (spec 9.2)."""
    return insert_source(
        conn,
        jurisdiction_id=city,
        body_id=body,
        type=MEETING_PORTAL,
        origin="https://portal.test.invalid",
        suggested_by="a person",
        reason="the body's agenda is published here",
        status="accepted",
    )


@pytest.fixture
def listed_meeting(conn: sqlite3.Connection, meeting: int, portal_source: int) -> int:
    """The meeting as a sync would have left it: portal-listed, with an agenda.

    The align job pauses on a meeting with no template id before it ever looks
    for a transcript, so the two portal columns are what make the pause below
    the pause this file is about.
    """
    conn.execute(
        "UPDATE meetings SET portal_source_id = ?, portal_html_template_id = ? WHERE id = ?",
        (portal_source, TEMPLATE_ID, meeting),
    )
    return meeting


# -- small helpers ------------------------------------------------------------


def take(conn: sqlite3.Connection, lane: str, clock: FakeClock) -> JobContext:
    """Claim the oldest job of one lane, or fail: the lane had nothing."""
    taken = claim(conn, lane, "worker-1", clock=clock)
    assert taken is not None, f"the {lane} lane had no job to claim"
    return JobContext(
        conn=conn,
        job_id=taken.job_id,
        kind=taken.kind,
        payload=taken.payload,
        lane=taken.lane,
        claim_token=taken.token,
        clock=clock,
    )


def state_of(conn: sqlite3.Connection, job_id: int) -> str:
    """The state of one job row."""
    row = get(conn, job_id)
    assert row is not None, f"there is no job {job_id}"
    return row["state"]


def reason_of(conn: sqlite3.Connection, job_id: int) -> str | None:
    """The last reason one job row carries."""
    row = get(conn, job_id)
    assert row is not None, f"there is no job {job_id}"
    return row["last_error"]


def jobs_of_any_kind(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every job of every kind, oldest first.

    A negative test is about nothing being asked for, so it reads the whole
    table rather than the align rows: an ask that named the wrong kind, or no
    meeting at all, still writes a row somewhere.
    """
    return list(conn.execute("SELECT * FROM jobs ORDER BY id").fetchall())


def align_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every align job, oldest first."""
    return list(
        conn.execute("SELECT * FROM jobs WHERE kind = ? ORDER BY id", (ALIGN_MEETING,)).fetchall()
    )


def transcripts_of(conn: sqlite3.Connection, video_id: int) -> list[sqlite3.Row]:
    """Every transcript of one video."""
    return list(
        conn.execute("SELECT * FROM transcripts WHERE video_id = ?", (video_id,)).fetchall()
    )


def a_paused_alignment(
    conn: sqlite3.Connection, meeting_id: int, source_id: int, clock: FakeClock
) -> int:
    """Queue a meeting's alignment and run it to the pause it has to take.

    The pause is the one this file makes true: the video of the meeting has no
    transcript yet. The reason it leaves is checked here, so a test below cannot
    pass by reviving a job that paused for some other reason.
    """
    job_id = enqueue(
        conn,
        ALIGN_MEETING,
        AlignRequest(meeting_id, source_id).as_payload(),
        lane="normal",
    )
    ctx = take(conn, "normal", clock)
    assert ctx.job_id == job_id, "the alignment took the lane first"
    with pytest.raises(JobPaused) as raised:
        align_meeting(ctx)
    assert "has no transcript yet" in str(raised.value)
    assert f"meeting {meeting_id}" in str(raised.value)
    assert state_of(conn, job_id) == PAUSED
    return job_id


def the_sister_transcript(
    conn: sqlite3.Connection, storage_root: Path, meeting_id: int, channel: int
) -> int:
    """Store the same meeting on another channel, with a transcript of its own.

    This is the video spec 8.4.1 calls the sister: another row of the same
    meeting. It is not the meeting's primary video, so storing its transcript is
    a write that must ask for nothing.
    """
    video_id = insert_video(
        conn,
        source_id=channel,
        meeting_id=meeting_id,
        platform_video_id="sisterCHAN1",
        title="City Council Regular Session (the other channel)",
        is_primary=False,
        readiness="finished",
    )
    store_transcript(conn, storage_root, video_id, b"<sister srv3/>")
    return video_id


def store_transcript(
    conn: sqlite3.Connection, storage_root: Path, video_id: int, data: bytes
) -> int:
    """Store a transcript of one video through the writer they all share.

    This is the path a caller takes when it has bytes and no more work to do.
    It stands in for nothing: the tests that prove an origin reaches the writer
    drive that origin's own caller, and this is only for the tests about what
    the writer must *not* do.
    """
    artifact = artifacts.store(conn, storage_root, artifacts.TRANSCRIPT, data, "srv3")
    return insert_transcript_with_segments(
        conn,
        video_id=video_id,
        artifact_id=artifact.id,
        origin="auto_captions",
        segments=SISTER_SEGMENTS,
    )


# -- captions (spec 8.3) ------------------------------------------------------


def test_a_stored_caption_transcript_asks_for_the_alignment(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    listed_meeting: int,
    portal_source: int,
) -> None:
    """The defect: the pause promised a re-run that nothing performed."""
    clock = FakeClock()
    job_id = a_paused_alignment(conn, listed_meeting, portal_source, clock)

    fake = FakeYtDlp()
    CaptionCapture(storage_root=storage_root, runner=fake)(claimed_job(conn, video, clock))

    assert repo.get_video(conn, video).capture_state == "captions"
    rows = transcripts_of(conn, video)
    assert len(rows) == 1 and rows[0]["origin"] == "auto_captions"
    assert state_of(conn, job_id) == QUEUED, "the transcript write put the alignment back"
    assert reason_of(conn, job_id) == TRANSCRIPT_STORED.format(
        video_id=video, meeting_id=listed_meeting
    )


def test_the_alignment_the_captions_revive_is_the_one_that_was_paused(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    listed_meeting: int,
    portal_source: int,
) -> None:
    """One meeting has one alignment: the paused row comes back, alone."""
    clock = FakeClock()
    job_id = a_paused_alignment(conn, listed_meeting, portal_source, clock)

    CaptionCapture(storage_root=storage_root, runner=FakeYtDlp())(claimed_job(conn, video, clock))

    assert [row["id"] for row in align_jobs(conn)] == [job_id]


# -- the sister channel (spec 8.4.1) ------------------------------------------


def test_a_sister_channel_transcript_asks_for_the_alignment(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    listed_meeting: int,
    portal_source: int,
    channel: int,
) -> None:
    """The primary video takes the other channel's transcript, and asks.

    The video whose captions failed here is the meeting's primary video, so the
    transcript it ends up with is the one the alignment was waiting for.
    """
    clock = FakeClock()
    the_sister_transcript(conn, storage_root, listed_meeting, channel)
    assert align_jobs(conn) == [], "a transcript of a video that is not primary asks nothing"
    job_id = a_paused_alignment(conn, listed_meeting, portal_source, clock)

    # yt-dlp wrote the sidecar and no caption file, which is the first hand-off.
    CaptionCapture(storage_root=storage_root, runner=FakeYtDlp(write_caption=False))(
        claimed_job(conn, video, clock)
    )

    rows = transcripts_of(conn, video)
    assert len(rows) == 1 and rows[0]["origin"] == SISTER_ORIGIN
    assert repo.get_video(conn, video).capture_state == "captions"
    assert state_of(conn, job_id) == QUEUED
    assert reason_of(conn, job_id) == TRANSCRIPT_STORED.format(
        video_id=video, meeting_id=listed_meeting
    )


# -- local speech to text (spec 8.5) ------------------------------------------


def test_a_local_transcription_asks_for_the_alignment(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    listed_meeting: int,
    portal_source: int,
) -> None:
    """The third origin reaches the same writer, and the writer asks."""
    clock = FakeClock()
    job_id = a_paused_alignment(conn, listed_meeting, portal_source, clock)
    tools = TwoPrograms(ytdlp=FakeYtDlp(audio=AUDIO_BYTES), textflowkit=FakeTextFlowKit())
    transcribe_id = enqueue(
        conn,
        TRANSCRIBE_KIND,
        payload_for(video, AudioTrigger.NO_CAPTIONS, HANDED_OFF),
        lane=TRANSCRIBE_LANE,
    )
    ctx = take(conn, TRANSCRIBE_LANE, clock)
    assert ctx.job_id == transcribe_id

    TranscribeAudio(
        storage_root=storage_root,
        runner=tools,
        ytdlp_interpreter="yt-dlp-python",
        textflowkit_program="textflowkit",
    )(ctx)

    rows = transcripts_of(conn, video)
    assert len(rows) == 1 and rows[0]["origin"] == "local_speech_to_text"
    assert state_of(conn, job_id) == QUEUED
    assert reason_of(conn, job_id) == TRANSCRIPT_STORED.format(
        video_id=video, meeting_id=listed_meeting
    )


# -- what asks for nothing ----------------------------------------------------


def test_a_video_with_no_meeting_asks_for_nothing(
    conn: sqlite3.Connection, storage_root: Path, channel: int
) -> None:
    """A video a watched channel captured is not yet any meeting's video."""
    loner = insert_video(
        conn,
        source_id=channel,
        platform_video_id="lonerVID01",
        readiness="finished",
    )
    assert repo.get_video(conn, loner).meeting_id is None

    store_transcript(conn, storage_root, loner, b"<srv3/>")

    assert not jobs_of_any_kind(conn), "no job of any kind was asked for"


def test_a_video_that_is_not_the_primary_video_asks_for_nothing(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    listed_meeting: int,
    portal_source: int,
    channel: int,
) -> None:
    """A second video of the meeting is not the one the alignment reads."""
    clock = FakeClock()
    job_id = a_paused_alignment(conn, listed_meeting, portal_source, clock)
    other = insert_video(
        conn,
        source_id=channel,
        meeting_id=listed_meeting,
        platform_video_id="secondVID02",
        is_primary=False,
        readiness="finished",
    )

    store_transcript(conn, storage_root, other, b"<srv3/>")

    assert state_of(conn, job_id) == PAUSED, "the alignment still has no transcript of video"
    assert reason_of(conn, job_id) is not None and "has no transcript yet" in reason_of(
        conn, job_id
    ), "and it was not touched"
    assert [row["id"] for row in align_jobs(conn)] == [job_id]


def test_the_ask_follows_whichever_video_is_primary(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    listed_meeting: int,
    channel: int,
) -> None:
    """Making another video primary moves the asking with it.

    The meeting here has no align job yet, so the asking is visible as a new
    row: the primary video's transcript is what puts one on the queue.
    """
    other = insert_video(
        conn,
        source_id=channel,
        meeting_id=listed_meeting,
        platform_video_id="secondVID02",
        is_primary=False,
        readiness="finished",
    )
    conn.execute("UPDATE videos SET is_primary = 0 WHERE id = ?", (video,))
    conn.execute("UPDATE videos SET is_primary = 1 WHERE id = ?", (other,))
    assert repo.primary_video(conn, listed_meeting) is not None
    assert repo.primary_video(conn, listed_meeting).id == other

    store_transcript(conn, storage_root, other, b"<the new primary srv3/>")

    waiting = align_jobs(conn)
    assert len(waiting) == 1, "a meeting with no alignment gets one for its primary video"
    assert (waiting[0]["state"], waiting[0]["lane"]) == (QUEUED, "normal")
    assert reason_of(conn, waiting[0]["id"]) is None, "a job made fresh carries no reason"

    # And the video that is no longer primary asks for nothing any more.
    store_transcript(conn, storage_root, video, b"<the old primary srv3/>")

    assert len(align_jobs(conn)) == 1, "the transcript of a video that is not primary asked nothing"


def test_two_rows_of_one_platform_video_are_told_apart(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    listed_meeting: int,
    portal_source: int,
) -> None:
    """Two sources can list one platform video; the row's own id is compared.

    The second row holds the same platform video id as the primary one and is
    not primary, so a check written against the platform id would ask here. A
    video row belongs to the source that listed it, so the twin comes from a
    second source; the check is against the row, and nothing is asked for.
    """
    clock = FakeClock()
    job_id = a_paused_alignment(conn, listed_meeting, portal_source, clock)
    twin = insert_video(
        conn,
        source_id=portal_source,
        meeting_id=listed_meeting,
        platform_video_id=PLATFORM_VIDEO_ID,
        is_primary=False,
        readiness="finished",
    )
    assert twin != video
    assert (
        repo.get_video(conn, twin).platform_video_id
        == repo.get_video(conn, video).platform_video_id
    )

    store_transcript(conn, storage_root, twin, b"<the twin's own srv3/>")

    assert state_of(conn, job_id) == PAUSED, "the alignment was waiting for the other row"
    assert [row["id"] for row in align_jobs(conn)] == [job_id]
