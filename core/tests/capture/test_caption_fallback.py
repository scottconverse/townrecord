"""What a capture does when the captions are missing or unusable (spec 8.4).

Captions come first (spec 8.3), and when a run ends with none -- or with a file
that parses to no lines -- the capture job hands off instead of reporting a
success nobody had. This file pins the order spec 8.4 fixes:

1. the sister channel (8.4.1), tried first and free;
2. the "Show transcript" panel (8.4.2), not implemented, named where the order
   is written so the gap stays visible;
3. the audio (8.4.3, 8.5), one ``transcribe`` job with the reason it exists.

Nothing here reaches YouTube, and no test downloads audio: the caption runner
is the fake of :mod:`tests.capture.fakes`, and the audio step is checked by the
job row it enqueued, never by running it. The transcription job itself is
tested in ``test_transcribe_job.py``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from townrecord import repo
from townrecord.capture import CaptionCapture, CaptureFailed, CaptureSettings, fallback
from townrecord.capture.job import LOCAL_TRANSCRIPTION_ONLY_REASON, NO_CAPTION_FILE
from townrecord.capture.transcribe import JOB_KIND as TRANSCRIBE_JOB_KIND
from townrecord.jobs import JobDeferred
from townrecord.repo import insert_video

from .fakes import PLATFORM_VIDEO_ID, FakeClock, FakeYtDlp, claimed_job, fake_429

#: A well-formed srv3 with an empty body: it parses, and it holds no line of
#: speech. This is the second hand-off trigger of spec 8.4, ``captions_unusable``.
EMPTY_SRV3 = (
    b'<?xml version="1.0" encoding="utf-8"?><timedtext format="3"><body></body></timedtext>'
)

#: The video id of the same meeting published on another watched channel.
SISTER_VIDEO_ID = "sister9QRS"


def capture(
    storage_root: Path,
    fake: FakeYtDlp,
    *,
    settings: CaptureSettings | None = None,
) -> CaptionCapture:
    """The caption handler under test, wired to the fake runner."""
    return CaptionCapture(
        storage_root=storage_root,
        settings=settings or CaptureSettings(),
        runner=fake,
        interpreter="python",
    )


def sister_video(conn: sqlite3.Connection, channel: int, meeting: int) -> int:
    """The same meeting on another watched channel (spec 8.4.1)."""
    return insert_video(
        conn,
        source_id=channel,
        meeting_id=meeting,
        platform_video_id=SISTER_VIDEO_ID,
        title="City Council Regular Session (other channel)",
        url=f"https://www.youtube.com/watch?v={SISTER_VIDEO_ID}",
        is_primary=False,
        readiness="finished",
    )


def transcripts(conn: sqlite3.Connection, video_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM transcripts WHERE video_id = ?", (video_id,)).fetchall()


def transcribe_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every ``transcribe`` job in the queue, oldest first."""
    return conn.execute(
        "SELECT * FROM jobs WHERE kind = ? ORDER BY id", (TRANSCRIBE_JOB_KIND,)
    ).fetchall()


def payload_of(row: sqlite3.Row) -> dict:
    return json.loads(row["payload"])


def handoff_of(conn: sqlite3.Connection, job_id: int) -> dict:
    """The hand-off record the capture job left in its checkpoint."""
    row = conn.execute("SELECT checkpoint FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row is not None and row["checkpoint"], "the capture job left no checkpoint"
    return json.loads(row["checkpoint"])[fallback.CHECKPOINT_KEY]


# -- the trigger (spec 8.4: never a silent success) ------------------------


def test_captions_that_parse_to_no_lines_hand_off_instead_of_succeeding(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """A caption file with nothing in it is not a capture, and it is not a crash."""
    clock = FakeClock()
    fake = FakeYtDlp(caption=EMPTY_SRV3)
    ctx = claimed_job(conn, video, clock)

    capture(storage_root, fake)(ctx)

    # Nothing was stored as a transcript of this video, and no failure was
    # raised either: the run handed off.
    assert transcripts(conn, video) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM artifacts").fetchone()["n"] == 0
    assert repo.get_video(conn, video).capture_state == "audio"

    jobs = transcribe_jobs(conn)
    assert len(jobs) == 1
    assert payload_of(jobs[0])["audio_trigger_reason"] == "captions_unusable"
    assert "parsed to no caption lines" in payload_of(jobs[0])["reason"]
    assert str(len(EMPTY_SRV3)) in payload_of(jobs[0])["reason"]

    recorded = handoff_of(conn, ctx.job_id)
    assert recorded["state"] == "audio"
    assert recorded["trigger"] == "captions_unusable"
    assert recorded["job_id"] == jobs[0]["id"]
    assert recorded["sister_video_id"] is None


def test_a_caption_file_that_cannot_be_read_hands_off_as_unusable(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """A file that is not a caption track at all is the same trigger, with its reason."""
    clock = FakeClock()
    fake = FakeYtDlp(caption=b"this is not a caption track\n")
    ctx = claimed_job(conn, video, clock)

    capture(storage_root, fake)(ctx)

    assert transcripts(conn, video) == []
    jobs = transcribe_jobs(conn)
    assert len(jobs) == 1
    assert payload_of(jobs[0])["audio_trigger_reason"] == "captions_unusable"
    assert handoff_of(conn, ctx.job_id)["reason"] == payload_of(jobs[0])["reason"]


# -- the sister channel, which is tried first (spec 8.4.1) -----------------


def test_the_sister_channel_is_used_before_any_audio_is_enqueued(
    conn: sqlite3.Connection,
    storage_root: Path,
    channel: int,
    meeting: int,
    video: int,
) -> None:
    """The same meeting on another channel already has a transcript: take that one."""
    clock = FakeClock()
    sister = sister_video(conn, channel, meeting)
    sister_fake = FakeYtDlp(platform_video_id=SISTER_VIDEO_ID)
    capture(storage_root, sister_fake)(
        claimed_job(
            conn,
            sister,
            clock,
        )
    )

    sister_transcript = transcripts(conn, sister)[0]
    assert sister_transcript["origin"] == "auto_captions"

    # The primary video's own run finds no caption file.
    fake = FakeYtDlp(write_caption=False)
    ctx = claimed_job(conn, video, clock)
    capture(storage_root, fake)(ctx)

    rows = transcripts(conn, video)
    assert len(rows) == 1
    assert rows[0]["origin"] == "sister_channel"
    assert rows[0]["artifact_id"] == sister_transcript["artifact_id"]
    assert rows[0]["is_provisional"] == sister_transcript["is_provisional"]

    mine = conn.execute(
        "SELECT text FROM segments WHERE transcript_id = ? ORDER BY start_ms", (rows[0]["id"],)
    ).fetchall()
    theirs = conn.execute(
        "SELECT text FROM segments WHERE transcript_id = ? ORDER BY start_ms",
        (sister_transcript["id"],),
    ).fetchall()
    assert [row["text"] for row in mine] == [row["text"] for row in theirs]
    assert len(mine) > 0

    # No audio: nothing was enqueued to download anything.
    assert transcribe_jobs(conn) == []
    assert repo.get_video(conn, video).capture_state == "captions"

    recorded = handoff_of(conn, ctx.job_id)
    assert recorded["state"] == "captions"
    assert recorded["sister_video_id"] == sister
    assert recorded["transcript_id"] == rows[0]["id"]
    assert recorded["job_id"] is None
    assert recorded["trigger"] == "no_captions"
    assert recorded["reason"] == NO_CAPTION_FILE

    # The hand-off downloaded nothing: the one command run was the caption
    # command of spec 8.3.
    assert len(fake.calls) == 1
    assert "--skip-download" in fake.argv
    for audio_flag in ("-x", "--extract-audio", "--audio-format", "--format"):
        assert audio_flag not in fake.argv


def test_a_video_whose_meeting_has_no_sister_still_gets_the_audio_job(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """A meeting with one captured video has no sister, so the fallback moves on."""
    clock = FakeClock()
    fake = FakeYtDlp(write_caption=False)
    ctx = claimed_job(conn, video, clock)

    capture(storage_root, fake)(ctx)

    jobs = transcribe_jobs(conn)
    assert len(jobs) == 1
    assert payload_of(jobs[0])["audio_trigger_reason"] == "no_captions"
    assert payload_of(jobs[0])["reason"] == NO_CAPTION_FILE
    assert repo.get_video(conn, video).capture_state == "audio"
    assert handoff_of(conn, ctx.job_id)["sister_video_id"] is None


def test_the_panel_of_spec_8_4_2_is_named_where_the_order_is_written() -> None:
    """8.4.2 is not implemented, and the module that owns the order says so."""
    source = Path(fallback.__file__).read_text(encoding="utf-8")
    assert "TODO: spec 8.4.2" in source


def test_a_hand_off_is_never_recorded_without_a_reason(
    conn: sqlite3.Connection, video: int
) -> None:
    """Spec 16.3: the user reads what happened, so a blank reason is refused."""
    clock = FakeClock()
    ctx = claimed_job(conn, video, clock)
    stored = repo.get_video(conn, video)
    assert stored is not None
    with pytest.raises(ValueError, match="never without one"):
        fallback.hand_off_captions(
            ctx, stored, reason="   ", trigger=fallback.AudioTrigger.NO_CAPTIONS
        )
    assert transcribe_jobs(conn) == []


# -- "Local transcription only" (spec 8.10) --------------------------------


def test_local_transcription_only_skips_the_caption_command(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """The switch turns captions off entirely: nothing is fetched, only queued."""
    clock = FakeClock()
    fake = FakeYtDlp()
    ctx = claimed_job(conn, video, clock)
    settings = CaptureSettings(local_transcription_only=True)

    capture(storage_root, fake, settings=settings)(ctx)

    # The caption command was never built and never run.
    assert fake.calls == []
    assert repo.get_video(conn, video).capture_state == "audio"

    jobs = transcribe_jobs(conn)
    assert len(jobs) == 1
    assert payload_of(jobs[0])["audio_trigger_reason"] == "local_transcription_only"
    assert payload_of(jobs[0])["reason"] == LOCAL_TRANSCRIPTION_ONLY_REASON
    recorded = handoff_of(conn, ctx.job_id)
    assert recorded["trigger"] == "local_transcription_only"
    assert recorded["job_id"] == jobs[0]["id"]
    # No sister is looked for: the captions were never asked for in the first
    # place, so there is nothing to fall back from.
    assert recorded["sister_video_id"] is None


def test_local_transcription_only_is_read_from_the_environment(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.10 is a setting, so it comes from the environment like the rest."""
    settings = CaptureSettings.from_env({"TOWNRECORD_CAPTURE_LOCAL_TRANSCRIPTION_ONLY": "on"})
    assert settings.local_transcription_only is True
    assert CaptureSettings.from_env({}).local_transcription_only is False


# -- HTTP 429 (spec 8.3) ---------------------------------------------------


def test_a_rate_limit_enqueues_no_transcription_at_all(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.3: 429 is a paced retry, and it is never a reason to fetch audio."""
    clock = FakeClock()
    fake = fake_429()
    with pytest.raises(JobDeferred):
        capture(storage_root, fake)(claimed_job(conn, video, clock))

    assert transcribe_jobs(conn) == []
    assert repo.get_video(conn, video).capture_state == "pending"
    assert conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 1


def test_a_capture_that_never_got_to_the_captions_hands_off_nothing(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """A plain yt-dlp failure is a failure with its reason, not a fallback."""
    clock = FakeClock()
    fake = FakeYtDlp(returncode=1, stderr="ERROR: unable to download video data\n")
    with pytest.raises(CaptureFailed):
        capture(storage_root, fake)(claimed_job(conn, video, clock))
    assert transcribe_jobs(conn) == []
    assert transcripts(conn, video) == []


def test_the_queued_job_names_the_video_it_was_handed_off_for(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """The fallback is for one video, and the job row says which."""
    clock = FakeClock()
    ctx = claimed_job(conn, video, clock)
    capture(storage_root, FakeYtDlp(write_caption=False))(ctx)
    jobs = transcribe_jobs(conn)
    assert len(jobs) == 1
    assert jobs[0]["lane"] == "heavy"
    assert payload_of(jobs[0])["video_id"] == video
    assert repo.get_video(conn, video).platform_video_id == PLATFORM_VIDEO_ID
    assert handoff_of(conn, ctx.job_id)["job_id"] == jobs[0]["id"]
