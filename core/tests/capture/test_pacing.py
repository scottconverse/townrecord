"""Every YouTube request the capture side makes waits on the process pace.

Spec 8.10 says "Pace every request", and the measured live run of 2026-09-27
made 7 captures and 7 status asks in 80 seconds from one address, so the pace
has to be wider than one command: a caption capture, an audio download and the
known-video test of spec 8.9 all ask YouTube, and each of them waits its turn.

The pace a test watches is the process pace of :mod:`tests.conftest`, which
records what each handler waited to do. A handler that never asks records
nothing, which is a failing assertion naming the missing behaviour
(PROJECT-BRIEF rule 11b).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.conftest import RecordingPacer
from townrecord.capture import CaptureSettings
from townrecord.capture.probe import capture_probe
from townrecord.capture.transcribe import TranscribeSettings
from townrecord.jobs import JobDeferred

from .fakes import RATE_LIMIT_STDERR, FakeClock, FakeYtDlp, claimed_job, fake_429
from .test_capture_job import capture
from .test_transcribe_job import handler_for, tools, transcribe_job

CAPTION = "a caption capture"
DOWNLOAD = "an audio download"
PROBE = "the known-video test"


def test_a_caption_capture_waits_for_the_pace(
    conn: sqlite3.Connection, storage_root: Path, video: int, pace: RecordingPacer
) -> None:
    """One command, one wait: the request is the whole of what a capture is."""
    capture(storage_root, FakeYtDlp())(claimed_job(conn, video, FakeClock()))

    assert pace.waits == [CAPTION]


def test_every_client_of_the_bot_check_ladder_waits(
    conn: sqlite3.Connection, storage_root: Path, video: int, pace: RecordingPacer
) -> None:
    """Spec 8.3's ladder is four requests, so it is four waits and not one.

    One wait for the ladder would let the three retries go out back to back,
    which is exactly the burst that was refused on 2026-09-27.
    """
    fake = FakeYtDlp(bot_check_until=())
    ctx = claimed_job(conn, video, FakeClock())

    with pytest.raises(JobDeferred):
        capture(storage_root, fake)(ctx)

    assert pace.waits == [CAPTION] * (1 + 3)
    assert len(fake.capture_calls) == 4


def test_a_rate_limited_capture_holds_the_other_youtube_jobs(
    conn: sqlite3.Connection, storage_root: Path, video: int, pace: RecordingPacer
) -> None:
    """HTTP 429 is YouTube answering for the address, not for this job."""
    ctx = claimed_job(conn, video, FakeClock())

    with pytest.raises(JobDeferred):
        capture(storage_root, fake_429())(ctx)

    assert pace.holds == [(CAPTION, CaptureSettings().rate_limit_retry_s)]


def test_an_audio_download_waits_for_the_pace(
    conn: sqlite3.Connection, storage_root: Path, video: int, pace: RecordingPacer
) -> None:
    """The audio command of spec 8.5 is a YouTube request, and it waits.

    The other child of the same job, ``textflowkit --version``, asks nothing of
    YouTube, so the whole list is one entry: a version ask that waited would
    hold captures behind a local program.
    """
    clock = FakeClock()
    runner = tools()

    handler_for(storage_root, runner)(transcribe_job(conn, video, clock))

    assert pace.waits == [DOWNLOAD]


def test_a_rate_limited_audio_download_holds_the_other_youtube_jobs(
    conn: sqlite3.Connection, storage_root: Path, video: int, pace: RecordingPacer
) -> None:
    """A 429 on the audio path holds the caption path too."""
    clock = FakeClock()
    runner = tools(returncode=1, stderr=RATE_LIMIT_STDERR)

    with pytest.raises(JobDeferred):
        handler_for(storage_root, runner)(transcribe_job(conn, video, clock))

    assert pace.holds == [(DOWNLOAD, TranscribeSettings().rate_limit_retry_s)]


def test_the_known_video_test_waits_for_the_pace(pace: RecordingPacer) -> None:
    """The test of spec 8.9 runs the same command, so it paces like a capture."""
    fake = FakeYtDlp()

    assert capture_probe(test_video_url="https://youtu.be/known", runner=fake)("python") is True

    assert pace.waits == [PROBE]
