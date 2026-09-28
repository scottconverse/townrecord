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

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.conftest import PACE_START, RecordingPacer
from townrecord.capture import CaptureSettings
from townrecord.capture.command import JOB_KIND
from townrecord.capture.probe import capture_probe
from townrecord.capture.transcribe import TranscribeSettings
from townrecord.jobs import DONE, QUEUED, JobDeferred, Registry, Runner, enqueue, get
from townrecord.jobs.queue import stamp as queue_stamp
from townrecord.pacing import DEFAULT_RATE_LIMIT_BACKOFF_S

from .fakes import RATE_LIMIT_STDERR, FakeClock, FakeYtDlp, claimed_job, fake_429
from .test_capture_job import capture
from .test_transcribe_job import handler_for, tools, transcribe_job

CAPTION = "a caption capture"
DOWNLOAD = "an audio download"
PROBE = "the known-video test"

#: The hold the live run of 2026-09-27 19:07 put on the pace: fifteen minutes,
#: far longer than a worker may spend waiting for something outside it.
HOLD_S = DEFAULT_RATE_LIMIT_BACKOFF_S

#: A hold shorter than the pace's own minimum gap. That one is the pace working
#: as it should, and it is still slept where it is.
SHORT_HOLD_S = 4.0

#: Where the reason is read from. Denver is UTC-6 in September, so the 12:15 UTC
#: the hold ends at is 06:15 there, which is the time a person reads.
DENVER = "America/Denver"
END_LOCAL = "06:15 local time (America/Denver)"


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


# -- a hold longer than a gap is the queue's wait, not a worker's -------------


def a_hold(pace: RecordingPacer, *, delay_s: float = HOLD_S) -> datetime:
    """Put a 429 hold on the process pace, where a person reads local time."""
    pace.time_zone = DENVER
    return pace.rate_limited(what=CAPTION, marker="http error 429", delay_s=delay_s)


def a_capture_runner(
    db_path: Path,
    storage_root: Path,
    fake: FakeYtDlp,
    pace: RecordingPacer,
    registry: Registry | None = None,
) -> Runner:
    """The real runner, with the capture handler wired to the fake yt-dlp.

    The runner reads the same clock as the pace, which is what the two readers
    of the time in one live process are: the injected clock is the only clock
    either of them may see, and no test here waits on the wall clock
    (PROJECT-BRIEF rule 4).
    """
    wired = registry or Registry()
    wired.register(JOB_KIND, capture(storage_root, fake))
    return Runner(db_path, registry=wired, clock=pace.clock)


def test_a_held_youtube_job_frees_its_worker_at_once(
    db_path: Path, conn: sqlite3.Connection, storage_root: Path, video: int, pace: RecordingPacer
) -> None:
    """The live run of 2026-09-27 19:07, where a worker slept the hold itself.

    Three jobs went through `run_once("normal")` in a row. The first capture was
    answered 429, and the capture behind it then slept all 900 seconds of the
    hold inside `run_once`, so one of the two normal-lane workers was out of the
    pool for fifteen minutes over a wait that had nothing to do with the work in
    it. That is what spec 16.1's lanes exist to prevent. The 429 here is the real
    one: the first job takes the hold itself and the second one finds it.
    """
    # A service knows the time zone of the person reading the queue, so the
    # sentence a deferred job carries is in their clock and not in UTC.
    pace.time_zone = DENVER
    registry = Registry()
    registry.register("scan", lambda ctx: None)
    fake = fake_429()
    runner = a_capture_runner(db_path, storage_root, fake, pace, registry)
    first = enqueue(conn, JOB_KIND, {"video_id": video})
    behind_it = enqueue(conn, JOB_KIND, {"video_id": video})
    no_youtube = enqueue(conn, "scan", {"body": "city"}, lane="normal")

    # The first capture is rate limited, which is what holds the process pace,
    # and it goes back in the queue for the back-off.
    assert runner.run_once("normal") == first
    assert get(conn, first)["state"] == QUEUED
    assert len(fake.capture_calls) == 1

    # The capture behind the same hold does not wait it out. It goes back in the
    # queue, due at the moment the hold lifts, and the worker is free now.
    assert runner.run_once("normal") == behind_it
    row = get(conn, behind_it)
    assert row["state"] == QUEUED
    assert row["run_after"] == queue_stamp(PACE_START + timedelta(seconds=HOLD_S))
    assert END_LOCAL in (row["last_error"] or "")
    assert pace.clock.slept == []
    assert len(fake.capture_calls) == 1, "a job behind a hold asked YouTube anyway"

    # And the same lane runs a job that asks YouTube nothing, while both
    # captures wait.
    assert runner.run_once("normal") == no_youtube
    assert get(conn, no_youtube)["state"] == DONE


def test_the_deferral_reason_names_the_hold_and_when_it_ends(
    db_path: Path, conn: sqlite3.Connection, storage_root: Path, video: int, pace: RecordingPacer
) -> None:
    """Spec 16.3: a job waiting fifteen minutes says why, in the reader's time."""
    until = a_hold(pace)
    fake = FakeYtDlp()
    runner = a_capture_runner(db_path, storage_root, fake, pace)
    job_id = enqueue(conn, JOB_KIND, {"video_id": video})

    assert runner.run_once("normal") == job_id

    row = get(conn, job_id)
    reason = row["last_error"] or ""
    assert row["state"] == QUEUED
    assert "a caption capture was rate limited (http error 429)" in reason
    assert END_LOCAL in reason
    assert row["run_after"] == queue_stamp(until)
    assert pace.clock.slept == []
    assert fake.capture_calls == []


def test_a_wait_under_one_gap_is_still_spent_where_it_is(
    db_path: Path,
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    pace: RecordingPacer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The other half of the rule: a short wait is the pace working, not a hold.

    A wait of seconds is what a pace is for, and it holds nothing worth freeing:
    the job waits its turn and runs. This is what keeps the rule above from
    becoming "a YouTube job never waits at all".
    """
    a_hold(pace, delay_s=SHORT_HOLD_S)
    fake = FakeYtDlp()
    runner = a_capture_runner(db_path, storage_root, fake, pace)
    job_id = enqueue(conn, JOB_KIND, {"video_id": video})

    with caplog.at_level(logging.INFO, logger="townrecord.capture.job"):
        assert runner.run_once("normal") == job_id

    assert pace.clock.slept == [SHORT_HOLD_S]
    assert get(conn, job_id)["state"] == DONE
    assert len(fake.capture_calls) == 1
    assert f"Capturing video {video} into" in caplog.text


def test_a_capture_says_so_only_once_the_pace_allows_it(
    db_path: Path,
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    pace: RecordingPacer,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The live log of 2026-09-27 19:07 announced a capture that never happened.

    "Capturing video 10 ..." was printed at 19:07:00, in the same second as the
    900-second hold, and that capture did not happen on that pass at all. A line
    that reads as a fact and is not one is spec 16.3's own failure.
    """
    a_hold(pace)
    fake = FakeYtDlp()
    runner = a_capture_runner(db_path, storage_root, fake, pace)
    job_id = enqueue(conn, JOB_KIND, {"video_id": video})

    with caplog.at_level(logging.INFO, logger="townrecord.capture.job"):
        assert runner.run_once("normal") == job_id

    assert "Capturing video" not in caplog.text
    assert fake.capture_calls == []
    assert get(conn, job_id)["state"] == QUEUED
