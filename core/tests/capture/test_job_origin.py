"""A job that queues work takes after the job that asked for it (spec 16.2).

``enqueue`` records how a job was asked for, and it takes no origin out of the
air: it defaults to ``manual``, because a job nothing preceded is a job the user
asked for. Every handler that queues work of its own has to hand its own origin
on, and a handler that says nothing is not neutral -- it records the child of a
scheduled run as something the user asked for. One caller did exactly that, and
this file holds every caller that has a job in hand.

Each case below drives the caller rather than the function it calls, because
what is being tested is who asks, not what asking does: the real capture job,
the real hand-off, the real transcription job. The two cases with no job behind
them are here for the other half of the rule -- a job nothing preceded is
manual, and the fix must not take that away.

Nothing here reaches YouTube and nothing runs a model: the runners are the fakes
of :mod:`tests.capture.fakes` (PROJECT-BRIEF rule 4).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from townrecord import artifacts, repo
from townrecord.captions.parse import Segment
from townrecord.capture import CaptionCapture, TranscribeAudio, insert_transcript_with_segments
from townrecord.capture.requests import RECHECK_JOB_KIND, request_recheck
from townrecord.capture.transcribe import JOB_KIND as TRANSCRIBE_KIND
from townrecord.capture.transcribe import LANE as TRANSCRIBE_LANE
from townrecord.capture.transcribe import payload_for
from townrecord.jobs import (
    ORIGIN_MANUAL,
    ORIGIN_SCHEDULED,
    JobContext,
    claim,
    enqueue,
    get,
)
from townrecord.records.portal import ALIGN_MEETING, READ_SPEAKERS
from townrecord.records.requests import request_alignment
from townrecord.repo import insert_person, insert_seat
from townrecord.stt.audio import AudioTrigger

from .fakes import FakeClock, FakeTextFlowKit, FakeYtDlp, claimed_job
from .test_align_after_transcript import AUDIO_BYTES, TwoPrograms

#: The sentence the hand-off of spec 8.4.3 records, as this file needs one.
HANDED_OFF = "the captions were unusable"

#: The lines of a transcript written by a caller that has no job of its own.
LINES = (Segment(start_ms=0, end_ms=1500, text="One line, one speaker."),)

#: The title the one seat of these tests is held under.
SEAT_TITLE = "Council Member"


# -- helpers ------------------------------------------------------------------


def take(conn: sqlite3.Connection, lane: str, clock: FakeClock) -> JobContext:
    """Claim the oldest job of one lane, or fail: the lane had nothing.

    The context is built from the claimed row's own origin, because that is what
    a handler reads. A helper that left it at the default would hand every job a
    ``manual`` context and hide the very rule this file is about.
    """
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
        origin=taken.origin,
    )


def the_only(conn: sqlite3.Connection, kind: str) -> sqlite3.Row:
    """The one job of a kind the run left behind, or fail saying how many."""
    rows = list(conn.execute("SELECT * FROM jobs WHERE kind = ? ORDER BY id", (kind,)).fetchall())
    assert len(rows) == 1, f"expected one {kind} job and found {len(rows)}"
    return rows[0]


def a_seat_on_the_body(conn: sqlite3.Connection, body_id: int) -> int:
    """Give a body one seat, so its meetings have somebody a line could be theirs.

    The speaker reading of spec 10.6 pauses on a body with no seat on the date
    of the meeting, and ``request_speakers`` asks for nothing in that case. A
    test about which origin the ask carries needs the ask to be made at all.
    """
    person = insert_person(conn, name="Ada Lovelace")
    return insert_seat(
        conn,
        person_id=person,
        body_id=body_id,
        title=SEAT_TITLE,
        start_date="2020-01-01",
    )


def origin_of(conn: sqlite3.Connection, job_id: int) -> str:
    """How one job row was asked for (spec 16.2)."""
    row = get(conn, job_id)
    assert row is not None, f"there is no job {job_id}"
    return row["origin"]


# -- the hand-off of spec 8.4.3 -----------------------------------------------


def test_the_transcription_a_hand_off_queues_takes_after_the_capture(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
) -> None:
    """The defect: a scheduled capture's transcription was recorded as manual."""
    clock = FakeClock()
    ctx = claimed_job(conn, video, clock, origin=ORIGIN_SCHEDULED)

    # yt-dlp wrote the sidecar and no caption file, and no other channel of the
    # meeting has a transcript, so step 3 of spec 8.4 is taken.
    CaptionCapture(storage_root=storage_root, runner=FakeYtDlp(write_caption=False))(ctx)

    assert repo.get_video(conn, video).capture_state == "audio"
    assert the_only(conn, TRANSCRIBE_KIND)["origin"] == ORIGIN_SCHEDULED, (
        "the transcription is the child of a scheduled capture"
    )


def test_a_transcription_a_manual_capture_queues_stays_manual(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
) -> None:
    """The other half: inheriting the origin must not make everything scheduled."""
    clock = FakeClock()
    ctx = claimed_job(conn, video, clock, origin=ORIGIN_MANUAL)

    CaptionCapture(storage_root=storage_root, runner=FakeYtDlp(write_caption=False))(ctx)

    assert the_only(conn, TRANSCRIBE_KIND)["origin"] == ORIGIN_MANUAL


# -- the first recheck of a provisional capture (spec 8.7) --------------------


def test_the_first_recheck_takes_after_the_capture_that_asked_for_it(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
) -> None:
    """A capture the user asked for queues a recheck the user asked for."""
    clock = FakeClock()
    ctx = claimed_job(conn, video, clock, origin=ORIGIN_MANUAL)

    CaptionCapture(storage_root=storage_root, runner=FakeYtDlp())(ctx)

    assert the_only(conn, RECHECK_JOB_KIND)["origin"] == ORIGIN_MANUAL


def test_the_first_recheck_of_a_scheduled_capture_is_scheduled(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
) -> None:
    """And a recheck the daily schedule led to is not recorded as the user's."""
    clock = FakeClock()
    ctx = claimed_job(conn, video, clock, origin=ORIGIN_SCHEDULED)

    CaptionCapture(storage_root=storage_root, runner=FakeYtDlp())(ctx)

    assert the_only(conn, RECHECK_JOB_KIND)["origin"] == ORIGIN_SCHEDULED


# -- what a stored transcript asks for (spec 16.3, 10.6) ----------------------


def test_the_asks_of_a_transcription_take_after_the_transcription_job(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    body: int,
) -> None:
    """The transcript writer's two asks are the transcribing job's asks.

    Both run inside the write, and the write knows only the connection: the
    origin has to be handed down from the job that called it, or the two jobs
    the transcript queues are recorded as asks nobody made.
    """
    clock = FakeClock()
    a_seat_on_the_body(conn, body)
    tools = TwoPrograms(ytdlp=FakeYtDlp(audio=AUDIO_BYTES), textflowkit=FakeTextFlowKit())
    job_id = enqueue(
        conn,
        TRANSCRIBE_KIND,
        payload_for(video, AudioTrigger.NO_CAPTIONS, HANDED_OFF),
        lane=TRANSCRIBE_LANE,
        origin=ORIGIN_SCHEDULED,
    )
    ctx = take(conn, TRANSCRIBE_LANE, clock)
    assert ctx.job_id == job_id
    assert ctx.origin == ORIGIN_SCHEDULED, "the context took the claimed job's origin"

    TranscribeAudio(
        storage_root=storage_root,
        runner=tools,
        ytdlp_interpreter="yt-dlp-python",
        textflowkit_program="textflowkit",
    )(ctx)

    assert origin_of(conn, the_only(conn, ALIGN_MEETING)["id"]) == ORIGIN_SCHEDULED
    assert origin_of(conn, the_only(conn, READ_SPEAKERS)["id"]) == ORIGIN_SCHEDULED


def test_a_transcript_stored_with_no_job_behind_it_asks_as_manual(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    body: int,
) -> None:
    """A writer called with no originating job leaves the default, not a guess."""
    a_seat_on_the_body(conn, body)
    artifact = artifacts.store(conn, storage_root, artifacts.TRANSCRIPT, b"<srv3/>", "srv3")

    insert_transcript_with_segments(
        conn,
        video_id=video,
        artifact_id=artifact.id,
        origin="auto_captions",
        segments=LINES,
    )

    assert origin_of(conn, the_only(conn, ALIGN_MEETING)["id"]) == ORIGIN_MANUAL
    assert origin_of(conn, the_only(conn, READ_SPEAKERS)["id"]) == ORIGIN_MANUAL


# -- a job nothing preceded ---------------------------------------------------


def test_a_job_nothing_preceded_is_manual(
    conn: sqlite3.Connection,
    video: int,
    meeting: int,
) -> None:
    """The default is the rule, not a fallback: no parent, no inherited origin."""
    assert origin_of(conn, enqueue(conn, TRANSCRIBE_KIND, {"video_id": video})) == ORIGIN_MANUAL

    recheck = request_recheck(conn, video)
    assert recheck is not None
    assert origin_of(conn, recheck) == ORIGIN_MANUAL

    alignment = request_alignment(conn, meeting)
    assert alignment is not None
    assert origin_of(conn, alignment) == ORIGIN_MANUAL
