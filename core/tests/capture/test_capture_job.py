"""The caption capture job end to end, with a fake yt-dlp (spec 8.2 to 8.7).

Every test here runs the handler with the fake runner of :mod:`tests.capture.
fakes`, which writes the files yt-dlp would write. No test reaches YouTube.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from townrecord import artifacts, proc, repo
from townrecord.capture import CaptionCapture, CaptureFailed, CaptureSettings
from townrecord.capture.archive import archive_path
from townrecord.capture.command import JOB_KIND, Runner
from townrecord.capture.job import BOT_CHECK_DEFERRAL, MISSING_SIDECAR_REASON
from townrecord.jobs import PAUSED, QUEUED, RUNNING, JobDeferred, JobPaused, get
from townrecord.jobs.queue import stamp
from townrecord.runtime.javascript import FALLBACK_RUNTIME

from .fakes import (
    INFO_AUTO_CAPTIONS,
    INFO_PUBLISHER_CAPTIONS,
    PLATFORM_VIDEO_ID,
    FakeClock,
    FakeYtDlp,
    claimed_job,
    deno_beside,
    fake_429,
    fake_bot_check,
    private_python,
)


def capture(
    storage_root: Path,
    runner: Runner | None,
    *,
    settings: CaptureSettings | None = None,
    interpreter: str = "python",
) -> CaptionCapture:
    """The handler under test, wired to the fake runner.

    ``interpreter="python"`` is a name and not a path, so unless a test passes a
    real path the JavaScript runtime resolves to the bare fallback name and the
    argument list does not depend on what this machine has installed.
    """
    return CaptionCapture(
        storage_root=storage_root,
        settings=settings or CaptureSettings(),
        runner=runner,
        interpreter=interpreter,
    )


def transcripts(conn: sqlite3.Connection, video_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM transcripts WHERE video_id = ?", (video_id,)).fetchall()


def segments(conn: sqlite3.Connection, transcript_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM segments WHERE transcript_id = ? ORDER BY start_ms", (transcript_id,)
    ).fetchall()


def job_of(conn: sqlite3.Connection, ctx) -> sqlite3.Row:
    row = get(conn, ctx.job_id)
    assert row is not None
    return row


# -- the capture itself ----------------------------------------------------


def test_a_finished_video_is_captured_and_stored(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """The whole happy path: run the command, store the artifacts, write the rows."""
    clock = FakeClock()
    fake = FakeYtDlp()
    ctx = claimed_job(conn, video, clock)

    capture(storage_root, fake)(ctx)

    assert len(fake.calls) == 1
    rows = transcripts(conn, video)
    assert len(rows) == 1
    transcript = rows[0]
    assert transcript["origin"] == "auto_captions"
    assert transcript["is_provisional"] == 1

    caption_artifact = conn.execute(
        "SELECT * FROM artifacts WHERE id = ?", (transcript["artifact_id"],)
    ).fetchone()
    assert caption_artifact["kind"] == "transcript"
    assert caption_artifact["rel_path"].endswith(".srv3")
    assert json.loads(caption_artifact["meta"]) == {
        "origin": "auto_captions",
        "video_id": PLATFORM_VIDEO_ID,
        # Spec 8.3, 8.9: the runtime that ran, and the client that answered, are
        # part of what the capture records about itself.
        "js_runtime": FALLBACK_RUNTIME,
        "player_client": "default",
        # Spec 8.7: the two signals of the change test that are not the hash,
        # kept here so the first recheck has a baseline to compare against. The
        # fixture sidecar states a length and no revision time, which is what a
        # real YouTube sidecar leaves: `revision_at` is None rather than a
        # moment nothing stated.
        "revision_at": None,
        "duration_s": 7200.0,
    }

    stored = segments(conn, int(transcript["id"]))
    assert [row["text"] for row in stored][:2] == [
        "I would now like to call the city of",
        "Longmont regular session September 8th,",
    ]
    assert [row["start_ms"] for row in stored][:2] == [3280, 5279]

    info_rows = conn.execute("SELECT * FROM artifacts WHERE kind = 'info'").fetchall()
    assert len(info_rows) == 1
    assert json.loads(info_rows[0]["meta"]) == {"video_id": PLATFORM_VIDEO_ID}

    assert repo.get_video(conn, video).capture_state == "captions"
    # The handler does not finish its own job: the runner does that once the
    # handler returns, which the Runner test at the end of this folder checks.
    assert job_of(conn, ctx)["state"] == RUNNING
    assert artifacts.missing_sidecars(conn) == []


def test_publisher_captions_are_told_from_automatic_ones(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """A track in `subtitles` is the publisher's; only `automatic_captions` is YouTube's."""
    clock = FakeClock()
    fake = FakeYtDlp(info=dict(INFO_PUBLISHER_CAPTIONS))
    capture(storage_root, fake)(claimed_job(conn, video, clock))

    assert transcripts(conn, video)[0]["origin"] == "publisher_captions"


def test_a_vtt_caption_file_is_parsed_when_there_is_no_srv3(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.3 asks for srv3 first and vtt second, and the job takes either."""
    clock = FakeClock()
    vtt = (
        Path(__file__).resolve().parents[1] / "captions" / "fixtures" / "youtube.en.vtt"
    ).read_bytes()
    fake = FakeYtDlp(caption=vtt, caption_ext="vtt")
    capture(storage_root, fake)(claimed_job(conn, video, clock))

    transcript = transcripts(conn, video)[0]
    artifact = conn.execute(
        "SELECT * FROM artifacts WHERE id = ?", (transcript["artifact_id"],)
    ).fetchone()
    assert artifact["rel_path"].endswith(".vtt")
    assert segments(conn, int(transcript["id"]))


def test_a_second_run_of_the_same_bytes_changes_nothing(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.7: the same captions again are the same artifact, transcript and lines."""
    clock = FakeClock()
    fake = FakeYtDlp()
    capture(storage_root, fake)(claimed_job(conn, video, clock))
    first = transcripts(conn, video)
    first_segments = segments(conn, int(first[0]["id"]))

    capture(storage_root, fake)(claimed_job(conn, video, clock))

    again = transcripts(conn, video)
    assert len(again) == 1
    assert again[0]["id"] == first[0]["id"]
    assert again[0]["artifact_id"] == first[0]["artifact_id"]
    assert conn.execute("SELECT COUNT(*) AS n FROM artifacts").fetchone()["n"] == 2
    assert segments(conn, int(again[0]["id"])) == first_segments


def test_a_missing_sidecar_is_recorded_and_not_a_silent_success(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.6: a capture without its info.json is recorded with its reason."""
    clock = FakeClock()
    fake = FakeYtDlp(info=None)
    with pytest.raises(CaptureFailed) as caught:
        capture(storage_root, fake)(claimed_job(conn, video, clock))

    assert "info.json" in str(caught.value)
    recorded = artifacts.missing_sidecars(conn, PLATFORM_VIDEO_ID)
    assert len(recorded) == 1
    assert recorded[0]["reason"] == MISSING_SIDECAR_REASON
    assert transcripts(conn, video) == []


def test_a_sidecar_with_no_english_track_stops_with_a_reason(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Nothing in the sidecar says who made the captions, so the job does not guess."""
    clock = FakeClock()
    fake = FakeYtDlp(info={"duration": 60, "subtitles": {}, "automatic_captions": {}})
    with pytest.raises(CaptureFailed):
        capture(storage_root, fake)(claimed_job(conn, video, clock))
    assert transcripts(conn, video) == []


# -- readiness (spec 8.2) --------------------------------------------------


def test_an_upcoming_video_goes_back_to_the_queue_with_a_reason(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.2: skip it, count it, and retry later. It is not a failure."""
    clock = FakeClock()
    fake = FakeYtDlp()
    repo.set_capture_state(conn, video, "pending")
    conn.execute("UPDATE videos SET readiness = 'upcoming' WHERE id = ?", (video,))
    ctx = claimed_job(conn, video, clock)

    with pytest.raises(JobDeferred) as caught:
        capture(storage_root, fake)(ctx)

    assert str(caught.value) == "upcoming, will retry"
    row = job_of(conn, ctx)
    assert row["state"] == QUEUED
    assert row["last_error"] == "upcoming, will retry"
    assert row["run_after"] == stamp(clock.now + timedelta(seconds=3600))
    assert row["claim_token"] is None
    assert repo.get_video(conn, video).capture_state == "skipped"
    assert fake.calls == []


def test_an_unknown_video_is_asked_and_then_captured(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.2: a row nothing has decided is asked, written down, and captured."""
    clock = FakeClock()
    fake = FakeYtDlp()
    conn.execute("UPDATE videos SET readiness = 'unknown' WHERE id = ?", (video,))
    ctx = claimed_job(conn, video, clock)

    capture(storage_root, fake)(ctx)

    asks = fake.status_asks
    assert len(asks) == 1, "the row was unknown, so exactly one status was asked for"
    assert asks[0]["argv"][2] == "yt_dlp"
    assert "--dump-single-json" in asks[0]["argv"], "the ask is spec 8.2's third step"
    assert "--skip-download" not in asks[0]["argv"], "the ask is not the capture command"
    assert asks[0]["argv"][-1].endswith(f"watch?v={PLATFORM_VIDEO_ID}")
    assert len(fake.capture_calls) == 1, "the capture ran after the status was answered"
    assert repo.get_video(conn, video).readiness == "finished", (
        "the answer is written on the row, so the next run reads it instead of asking again"
    )
    assert transcripts(conn, video) != []


def test_an_upcoming_video_is_not_asked_and_not_captured(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """A row that already carries a definite word is not asked again (spec 8.2)."""
    clock = FakeClock()
    fake = FakeYtDlp()
    conn.execute("UPDATE videos SET readiness = 'upcoming' WHERE id = ?", (video,))
    ctx = claimed_job(conn, video, clock)

    with pytest.raises(JobDeferred):
        capture(storage_root, fake)(ctx)

    assert fake.status_asks == [], "upcoming is an answer, so nothing is asked"
    assert fake.calls == []


def test_a_video_whose_status_stays_unknown_defers_and_says_what_it_asked(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """An ask that decides nothing still defers, and the reason names every ask.

    ``post_live`` is the answer that is honest and useless: the stream ended and
    the recording is still being written, so there is nothing to download yet.
    The job must not call that a capture and must not call it a failure, and the
    user must be able to read which steps were asked and what each one said.
    """
    clock = FakeClock()
    fake = FakeYtDlp(status={"id": PLATFORM_VIDEO_ID, "live_status": "post_live"})
    conn.execute("UPDATE videos SET readiness = 'unknown' WHERE id = ?", (video,))
    ctx = claimed_job(conn, video, clock)

    with pytest.raises(JobDeferred) as caught:
        capture(storage_root, fake)(ctx)

    reason = str(caught.value)
    assert reason.startswith("waiting for status metadata (will retry). Asked:")
    assert "the official API: no API key was given" in reason
    assert "the player endpoint status: not built here" in reason
    assert "yt-dlp --dump-single-json: live_status=post_live" in reason
    assert reason.endswith(".")
    assert len(reason) < 300, "the reason is not truncated, so nothing asked is lost"
    assert len(fake.status_asks) == 1
    assert fake.capture_calls == [], "a video whose status is still unknown is not captured"
    assert repo.get_video(conn, video).readiness == "unknown", (
        "an ask that decided nothing does not overwrite what the row holds"
    )
    assert repo.get_video(conn, video).capture_state == "pending"
    assert job_of(conn, ctx)["state"] == QUEUED


def test_a_status_ask_that_fails_says_so_and_still_defers(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """yt-dlp failing the ask is a reason, not a crash: the job waits and says why."""
    clock = FakeClock()
    fake = FakeYtDlp(status_returncode=1, status_stderr="ERROR: [youtube] abc123XYZ: nope\n")
    conn.execute("UPDATE videos SET readiness = 'unknown' WHERE id = ?", (video,))
    ctx = claimed_job(conn, video, clock)

    with pytest.raises(JobDeferred) as caught:
        capture(storage_root, fake)(ctx)

    reason = str(caught.value)
    assert "yt-dlp --dump-single-json: it exited 1: ERROR: [youtube] abc123XYZ: nope" in reason
    assert fake.capture_calls == []
    assert repo.get_video(conn, video).readiness == "unknown"


def test_a_live_video_waits_and_is_counted_as_skipped(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    clock = FakeClock()
    fake = FakeYtDlp()
    conn.execute("UPDATE videos SET readiness = 'live' WHERE id = ?", (video,))
    with pytest.raises(JobDeferred):
        capture(storage_root, fake)(claimed_job(conn, video, clock))
    assert repo.get_video(conn, video).capture_state == "skipped"


# -- rate limiting (spec 8.3) ----------------------------------------------


def test_a_rate_limit_is_a_paced_retry_and_never_audio(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.3: 429 means paced retry on a later pass, NOT a reason to grab audio."""
    clock = FakeClock()
    fake = fake_429()
    ctx = claimed_job(conn, video, clock)

    with pytest.raises(JobDeferred) as caught:
        capture(storage_root, fake)(ctx)

    assert str(caught.value) == "rate limited by YouTube (http error 429), will retry later"
    row = job_of(conn, ctx)
    assert row["state"] == QUEUED
    assert row["run_after"] is not None
    assert row["run_after"] > stamp(clock.now)
    assert row["last_error"] == str(caught.value)

    # Never audio: one command was run, it was the caption command, and no
    # other job was enqueued to fetch anything at all.
    assert len(fake.calls) == 1
    assert fake.player_clients == [""]
    assert "--skip-download" in fake.argv
    for audio_flag in ("-x", "--extract-audio", "--audio-format", "--format"):
        assert audio_flag not in fake.argv
    assert conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 1
    assert repo.get_video(conn, video).capture_state == "pending"
    assert transcripts(conn, video) == []


def test_a_rate_limit_quotes_the_words_yt_dlp_printed(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """The reason the user reads carries the text that was matched."""
    from townrecord.capture.command import rate_limit_marker

    assert rate_limit_marker(fake_429().stderr) == "http error 429"


def test_another_failure_is_a_failure_with_the_last_line_of_stderr(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    clock = FakeClock()
    fake = FakeYtDlp(returncode=1, stderr="ERROR: unable to download video data\n")
    with pytest.raises(CaptureFailed) as caught:
        capture(storage_root, fake)(claimed_job(conn, video, clock))
    assert str(caught.value) == ("yt-dlp exited with 1: ERROR: unable to download video data")
    assert transcripts(conn, video) == []


# -- the JavaScript runtime (spec 8.3, 8.9) --------------------------------


def test_the_capture_gives_yt_dlp_the_runtime_of_the_private_venv(
    conn: sqlite3.Connection, storage_root: Path, video: int, tmp_path: Path
) -> None:
    """Spec 8.3, 8.9: the runtime comes from the venv, not from the user's PATH."""
    python = private_python(tmp_path)
    fake = FakeYtDlp()
    capture(storage_root, fake, interpreter=str(python))(claimed_job(conn, video, FakeClock()))

    deno = deno_beside(python)
    assert fake.argv[fake.argv.index("--js-runtimes") + 1] == f"deno:{deno}"

    transcript = transcripts(conn, video)[0]
    artifact = conn.execute(
        "SELECT * FROM artifacts WHERE id = ?", (transcript["artifact_id"],)
    ).fetchone()
    # Which runtime ran is part of what the capture records about itself.
    assert json.loads(artifact["meta"])["js_runtime"] == f"deno:{deno}"


def test_a_private_venv_with_no_javascript_program_uses_the_fallback_name(
    conn: sqlite3.Connection, storage_root: Path, video: int, tmp_path: Path
) -> None:
    """An older runtime the user still has names no path that does not exist."""
    python = private_python(tmp_path, deno=False)
    fake = FakeYtDlp()
    capture(storage_root, fake, interpreter=str(python))(claimed_job(conn, video, FakeClock()))

    assert fake.argv[fake.argv.index("--js-runtimes") + 1] == FALLBACK_RUNTIME


# -- the bot check (spec 8.3) ----------------------------------------------


def test_a_bot_check_is_answered_by_the_first_client_that_works(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.3: one retry per client, in order, and the first that answers wins."""
    clock = FakeClock()
    fake = fake_bot_check("visionos")
    ctx = claimed_job(conn, video, clock)

    capture(storage_root, fake)(ctx)

    assert fake.player_clients == ["", "android_vr", "visionos"]
    last = fake.calls[-1]["argv"]
    assert last[last.index("--extractor-args") + 1] == "youtube:player_client=visionos"
    # The winning client is the one recorded, and the capture did store a
    # transcript: a retry that works is the capture that happened.
    transcript = transcripts(conn, video)[0]
    artifact = conn.execute(
        "SELECT * FROM artifacts WHERE id = ?", (transcript["artifact_id"],)
    ).fetchone()
    assert json.loads(artifact["meta"])["player_client"] == "visionos"
    assert repo.get_video(conn, video).capture_state == "captions"
    assert job_of(conn, ctx)["state"] == RUNNING


def test_a_bot_check_no_client_answers_waits_and_never_audio(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.3, 8.10: every client refused is a deferral, never a failure or audio."""
    clock = FakeClock()
    fake = fake_bot_check()
    ctx = claimed_job(conn, video, clock)

    with pytest.raises(JobDeferred) as caught:
        capture(storage_root, fake)(ctx)

    assert fake.player_clients == ["", "android_vr", "visionos", "tv_embedded"]
    assert str(caught.value) == BOT_CHECK_DEFERRAL.format(tried="android_vr, visionos, tv_embedded")
    row = job_of(conn, ctx)
    assert row["state"] == QUEUED
    assert row["run_after"] > stamp(clock.now)
    assert row["last_error"] == str(caught.value)
    # Not a failure, and never audio: nothing else was run or queued, and the
    # video is exactly where it was.
    assert conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 1
    assert repo.get_video(conn, video).capture_state == "pending"
    assert transcripts(conn, video) == []
    for command in (fake.argv,):
        for audio_flag in ("-x", "--extract-audio", "--audio-format"):
            assert audio_flag not in command


def test_a_rate_limit_during_a_retry_stops_the_ladder(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Only a bot check goes round the ladder; a 429 ends it where it stands."""
    clock = FakeClock()
    fake = FakeYtDlp(bot_check_until=(), rate_limit_clients=("android_vr",))
    ctx = claimed_job(conn, video, clock)

    with pytest.raises(JobDeferred) as caught:
        capture(storage_root, fake)(ctx)

    assert fake.player_clients == ["", "android_vr"]
    assert str(caught.value) == "rate limited by YouTube (http error 429), will retry later"
    assert job_of(conn, ctx)["state"] == QUEUED
    assert transcripts(conn, video) == []


# -- the download archive (spec 8.7) ---------------------------------------


def test_a_stale_archive_cannot_hide_a_video(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """The archive is written from the database before the run, not read back."""
    stale = archive_path(storage_root)
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(f"youtube {PLATFORM_VIDEO_ID}\n", encoding="utf-8")

    clock = FakeClock()
    fake = FakeYtDlp()
    capture(storage_root, fake)(claimed_job(conn, video, clock))

    assert not fake.skipped_by_archive, "the stale archive file hid the video from yt-dlp"
    assert len(fake.calls) == 1, "the capture was skipped because of a stale archive file"
    assert fake.argv[fake.argv.index("--download-archive") + 1] == stale.as_posix()
    assert transcripts(conn, video), "the video was not captured"
    # The stale line was replaced by what the database said when the run
    # started, which was nothing: the video had no transcript yet.
    assert stale.read_text(encoding="utf-8") == ""

    # On the run after this one the database does say the video is captured,
    # so the archive names it and yt-dlp will skip it from then on.
    capture(storage_root, fake)(claimed_job(conn, video, clock))
    assert stale.read_text(encoding="utf-8") == f"youtube {PLATFORM_VIDEO_ID}\n"


def test_the_archive_lists_only_videos_that_have_a_transcript(
    conn: sqlite3.Connection, storage_root: Path, video: int, channel: int
) -> None:
    other = repo.insert_video(
        conn,
        source_id=channel,
        platform_video_id="zzz999",
        title="Another session",
        readiness="finished",
    )
    fake = FakeYtDlp()
    capture(storage_root, fake)(claimed_job(conn, video, FakeClock()))
    # The second capture writes the archive before it runs, so the file says
    # which videos had a transcript at that moment: the first one, not this one.
    capture(storage_root, FakeYtDlp(platform_video_id="zzz999"))(
        claimed_job(conn, other, FakeClock())
    )

    text = archive_path(storage_root).read_text(encoding="utf-8")
    assert text == f"youtube {PLATFORM_VIDEO_ID}\n"
    assert transcripts(conn, other)


def test_the_archive_leaves_out_a_host_it_does_not_know(
    conn: sqlite3.Connection, storage_root: Path, city: int, body: int
) -> None:
    """A guessed extractor key could hide a meeting, so no line is written."""
    other_source = repo.insert_source(
        conn,
        jurisdiction_id=city,
        body_id=body,
        type="video_channel",
        origin="https://videos.test.invalid/channel/UC-other",
        suggested_by="discovery",
        reason="A channel on a host TownRecord has no extractor for.",
        status="accepted",
    )
    other_video = repo.insert_video(
        conn,
        source_id=other_source,
        platform_video_id="other-1",
        title="Elsewhere",
        readiness="finished",
    )
    fake = FakeYtDlp()
    capture(storage_root, fake)(claimed_job(conn, other_video, FakeClock()))

    assert archive_path(storage_root).read_text(encoding="utf-8") == ""
    assert transcripts(conn, other_video)


def test_a_partial_capture_resumes_in_the_same_folder(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.3: a stopped capture resumes with --continue in the same folder."""
    folder = storage_root / "work" / PLATFORM_VIDEO_ID
    folder.mkdir(parents=True)
    (folder / f"{PLATFORM_VIDEO_ID}.en.srv3.part").write_bytes(b"<timedtext>")

    fake = FakeYtDlp()
    capture(storage_root, fake)(claimed_job(conn, video, FakeClock()))

    assert "--continue" in fake.argv
    assert fake.argv[fake.argv.index("--continue") - 1] == folder.as_posix()


def test_a_fresh_capture_is_not_a_resume(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    fake = FakeYtDlp()
    capture(storage_root, fake)(claimed_job(conn, video, FakeClock()))
    assert "--continue" not in fake.argv


# -- the size and length limits (spec 8.3) ---------------------------------


def test_a_meeting_over_the_length_limit_is_refused_with_its_reason(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.3: over 8 hours needs the user's confirmation, and says so."""
    clock = FakeClock()
    info = {**INFO_AUTO_CAPTIONS, "duration": 8 * 3600 + 60}
    fake = FakeYtDlp(info=info)
    ctx = claimed_job(conn, video, clock)

    with pytest.raises(JobPaused):
        capture(storage_root, fake)(ctx)

    row = job_of(conn, ctx)
    assert row["state"] == PAUSED
    assert row["last_error"] == (
        "Refused: the meeting runs 8h 1m, over the 8h limit. Capturing it needs your confirmation."
    )
    assert transcripts(conn, video) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM artifacts").fetchone()["n"] == 0
    assert repo.get_video(conn, video).capture_state == "pending"


def test_a_capture_over_the_size_limit_is_refused_with_its_reason(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.3: over 500 MB by default needs the user's confirmation too."""
    clock = FakeClock()
    fake = FakeYtDlp(padding_bytes=4096)
    settings = CaptureSettings(max_capture_bytes=1000)
    ctx = claimed_job(conn, video, clock)

    with pytest.raises(JobPaused):
        capture(storage_root, fake, settings=settings)(ctx)

    reason = job_of(conn, ctx)["last_error"]
    assert reason.startswith("Refused: the capture is ")
    assert reason.endswith("Capturing it needs your confirmation.")
    assert "the 0.000953674 MB limit" in reason
    assert transcripts(conn, video) == []


def test_a_sidecar_with_no_duration_is_not_refused_for_length(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """The job cannot invent a duration, and the size still guards the capture."""
    fake = FakeYtDlp(info={k: v for k, v in INFO_AUTO_CAPTIONS.items() if k != "duration"})
    capture(storage_root, fake)(claimed_job(conn, video, FakeClock()))
    assert transcripts(conn, video)


# -- the environment (PROJECT-BRIEF rule E) --------------------------------


def test_the_child_gets_no_secret_from_this_process(
    conn: sqlite3.Connection, storage_root: Path, video: int, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-yt-dlp")
    monkeypatch.setenv("PYTHONPATH", "C:/somewhere/else")
    fake = FakeYtDlp()
    capture(storage_root, fake)(claimed_job(conn, video, FakeClock()))

    assert "OPENAI_API_KEY" not in fake.env
    assert "PYTHONPATH" not in fake.env
    assert set(fake.env) <= set(proc.ALLOWED_ENV_NAMES)


def test_a_payload_that_is_not_a_video_id_fails_with_a_reason(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    from townrecord.jobs import claim, enqueue
    from townrecord.jobs.queue import JobContext

    clock = FakeClock()
    job_id = enqueue(conn, JOB_KIND, {"nonsense": 1})
    taken = claim(conn, "normal", "worker-1", clock=clock)
    assert taken is not None and taken.job_id == job_id
    ctx = JobContext(
        conn=conn,
        job_id=taken.job_id,
        kind=taken.kind,
        payload=taken.payload,
        lane=taken.lane,
        claim_token=taken.token,
        clock=clock,
    )
    with pytest.raises(CaptureFailed) as caught:
        capture(storage_root, FakeYtDlp())(ctx)
    assert "not a video id" in str(caught.value)
