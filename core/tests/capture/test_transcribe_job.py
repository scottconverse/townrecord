"""The audio fallback of spec 8.4.3 and 8.5: download it, transcribe it here.

Two things are checked here that the fallback tests cannot check, because they
stop at the job row: that the ``transcribe`` job does the whole of spec 8.5, and
that its transcript reaches the database through *the same* insert function the
caption path uses -- spec 8.5 allows no second code path, and a spy is the only
way to see which function a run actually called.

Nothing here runs yt-dlp, TextFlowKit or a model. The runner is the pair of
fakes of :mod:`tests.capture.fakes`, chosen by the program name the way a real
machine chooses between two programs, and the audio folder is a work folder on
disk like any other. The one test that uses the real
:class:`~townrecord.jobs.Runner` uses it with those same fakes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from townrecord import artifacts, repo
from townrecord.capture import (
    TRANSCRIBE_JOB_KIND,
    TRANSCRIBE_LANE,
    TranscribeAudio,
    TranscribeSettings,
    fallback,
    job,
    register_transcribe,
    store,
)
from townrecord.capture import transcribe as transcribe_module
from townrecord.capture.job import CaptureFailed
from townrecord.capture.transcribe import (
    JOB_KIND,
    LANE,
    NO_SPEECH,
    NO_TRANSCRIPT_FILE,
    payload_for,
    version_of_line,
)
from townrecord.jobs import (
    DONE,
    JobContext,
    JobDeferred,
    JobPaused,
    Registry,
    Runner,
    claim,
    enqueue,
    finish,
    get,
)
from townrecord.runtime.javascript import FALLBACK_RUNTIME, JavaScriptRuntime
from townrecord.runtime.settings import (
    TEXTFLOWKIT_PROGRAM,
    TEXTFLOWKIT_TOOL,
    TOOL_NAME,
)
from townrecord.runtime.tools import program_in, python_in
from townrecord.stt.audio import AudioTrigger
from townrecord.stt.provenance import LOCAL_SPEECH_TO_TEXT, Provenance

from .fakes import (
    PLATFORM_VIDEO_ID,
    RATE_LIMIT_STDERR,
    FakeClock,
    FakeTextFlowKit,
    FakeYtDlp,
    transcript_document,
)

#: The audio bytes the fake download writes. A real opus file is not needed:
#: the job hashes whatever arrived and nothing here decodes it.
AUDIO_BYTES = b"\x1aE\xdf\xa3 not really opus, but it hashes like it\x00\x01\x02"

#: The two programs of the private runtime of spec 8.9. They are built with the
#: two helpers the runtime itself uses, so ``Scripts``/``bin`` and ``.exe`` are
#: decided the way production decides them: this file then says the same thing
#: on Windows, macOS and Linux (PROJECT-BRIEF rule 11b).
RUNTIME_ROOT = Path("townrecord-runtimes")
YTDLP_VENV = RUNTIME_ROOT / TOOL_NAME / "2026.08.19"
TEXTFLOWKIT_VENV = RUNTIME_ROOT / TEXTFLOWKIT_TOOL / "0.1.6"
YTDLP_PYTHON = python_in(YTDLP_VENV)
TEXTFLOWKIT_EXE = program_in(TEXTFLOWKIT_VENV, TEXTFLOWKIT_PROGRAM)

#: The sentence the capture job of spec 8.4 hands off with, as this file needs
#: one; the job's own spelling is checked in ``test_caption_fallback.py``.
HANDED_OFF = "the captions were unusable"


@dataclass
class RuntimeTools:
    """One runner for the two children a transcription job starts.

    A real machine runs two different programs out of two different runtimes,
    and this dispatches the same way: by the program the argv names. Anything
    that is not the TextFlowKit console script is the yt-dlp runtime, which is
    where the audio download belongs.
    """

    ytdlp: FakeYtDlp
    textflowkit: FakeTextFlowKit

    def __call__(self, argv: Sequence[str], *, timeout_s: float, env: Mapping[str, str]) -> Any:
        command = [str(part) for part in argv]
        if Path(command[0]).stem.lower().startswith("textflowkit"):
            return self.textflowkit(command, timeout_s=timeout_s, env=env)
        return self.ytdlp(command, timeout_s=timeout_s, env=env)

    @property
    def transcribe_argv(self) -> list[str]:
        return self.textflowkit.argv

    @property
    def download_argv(self) -> list[str]:
        return self.ytdlp.argv


def tools(*, document: dict[str, Any] | None = None, **kwargs: Any) -> RuntimeTools:
    """The pair of fakes, with the download in audio mode.

    ``document`` is passed only when a test names one, so the fake's own
    fixture stays in use otherwise: handing it None would mean an empty file,
    which is a different test entirely.
    """
    transcriber = FakeTextFlowKit() if document is None else FakeTextFlowKit(document=document)
    return RuntimeTools(ytdlp=FakeYtDlp(audio=AUDIO_BYTES, **kwargs), textflowkit=transcriber)


def handler_for(storage_root: Path, runner: RuntimeTools, **kwargs: Any) -> TranscribeAudio:
    """The job under test, wired to the two fakes and the two runtime paths."""
    return TranscribeAudio(
        storage_root=storage_root,
        settings=kwargs.pop("settings", None) or TranscribeSettings(),
        runner=runner,
        ytdlp_interpreter=str(YTDLP_PYTHON),
        textflowkit_program=str(TEXTFLOWKIT_EXE),
        **kwargs,
    )


def transcribe_job(
    conn: sqlite3.Connection,
    video_id: int,
    clock: FakeClock,
    *,
    trigger: AudioTrigger = AudioTrigger.NO_CAPTIONS,
    reason: str = HANDED_OFF,
) -> JobContext:
    """Enqueue one transcription job on its own lane, claim it, return its context."""
    job_id = enqueue(
        conn,
        JOB_KIND,
        payload_for(video_id, trigger, reason),
        lane=LANE,
    )
    taken = claim(conn, LANE, "worker-1", clock=clock)
    assert taken is not None
    assert taken.job_id == job_id
    return JobContext(
        conn=conn,
        job_id=taken.job_id,
        kind=taken.kind,
        payload=taken.payload,
        lane=taken.lane,
        claim_token=taken.token,
        clock=clock,
    )


def done(conn: sqlite3.Connection, ctx: JobContext, clock: FakeClock) -> None:
    """Close a job the test ran by hand, so its lane is free for the next one."""
    finish(conn, ctx.job_id, ctx.claim_token, DONE, clock=clock)


def checkpoint(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT checkpoint FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row is not None and row["checkpoint"], "the job left no checkpoint"
    return json.loads(row["checkpoint"])


def transcript_rows(conn: sqlite3.Connection, video_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM transcripts WHERE video_id = ?", (video_id,)).fetchall()


def lines_of(conn: sqlite3.Connection, transcript_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM segments WHERE transcript_id = ? ORDER BY start_ms", (transcript_id,)
    ).fetchall()


def artifact_meta(conn: sqlite3.Connection, storage_root: Path, artifact_id: int) -> dict[str, Any]:
    stored = artifacts.get(conn, artifact_id, storage_root)
    assert stored is not None
    return stored.meta


# -- the whole of spec 8.5 in one run --------------------------------------


def test_the_audio_is_transcribed_and_stored_as_a_transcript(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Download with the runtime's yt-dlp, transcribe, store, mark captured."""
    clock = FakeClock()
    runner = tools()
    ctx = transcribe_job(conn, video, clock)

    handler_for(storage_root, runner)(ctx)

    # 1. the reason was recorded, and recorded first.
    recorded = checkpoint(conn, ctx.job_id)
    assert recorded["audio_trigger_reason"] == "no_captions"
    assert recorded["reason"] == HANDED_OFF

    # 2. the tool that made it, read from the runtime.
    version_call = runner.textflowkit.version_call
    assert version_call is not None
    assert version_call["argv"] == [str(TEXTFLOWKIT_EXE), "--version"]

    # 3. the download ran out of the *runtime's* interpreter, exactly once, and
    #    it is the audio-only command of spec 8.5.
    assert len(runner.ytdlp.calls) == 1
    download = runner.download_argv
    assert download[0] == str(YTDLP_PYTHON)
    assert download[1:3] == ["-m", "yt_dlp"]
    assert "-x" in download
    assert "--skip-download" not in download
    assert download.count("-o") == 1
    assert download[download.index("-o") + 1].endswith("%(id)s.%(ext)s")

    # 4. the audio was hashed from the bytes that arrived, and it is still on
    #    the user's machine (spec 8.10).
    digest = hashlib.sha256(AUDIO_BYTES).hexdigest()
    audio_path = storage_root / "work" / PLATFORM_VIDEO_ID / "audio" / f"{PLATFORM_VIDEO_ID}.opus"
    assert audio_path.is_file()
    assert recorded["audio_sha256"] == digest
    assert recorded["audio_path"] == f"work/{PLATFORM_VIDEO_ID}/audio/{PLATFORM_VIDEO_ID}.opus"

    # 5. the transcription ran with the argv of spec 8.5 and the timeout of
    #    spec 8.5, which is 1.5 x the length of the audio plus ten minutes.
    transcribe = runner.transcribe_argv
    assert transcribe[0] == str(TEXTFLOWKIT_EXE)
    assert transcribe[1] == "transcribe"
    assert "--output-dir" in transcribe
    assert runner.textflowkit.transcribe_calls[0]["timeout_s"] == 1.5 * 7200 + 600

    # 6. the transcript is the meeting's, with the origin of a local capture.
    rows = transcript_rows(conn, video)
    assert len(rows) == 1
    assert rows[0]["origin"] == LOCAL_SPEECH_TO_TEXT
    assert rows[0]["artifact_id"] is not None
    lines = lines_of(conn, rows[0]["id"])
    assert [line["text"] for line in lines] == [
        "The council will come to order.",
        "Roll call, please.",
    ]
    assert repo.get_video(conn, video).capture_state == "audio"
    assert recorded["transcript_id"] == rows[0]["id"]
    assert recorded["segments"] == 2


def test_the_audio_download_is_told_which_javascript_runtime_to_use(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.3, 8.9: the runtime is resolved from the yt-dlp interpreter.

    The interpreter of this file sits under a made-up runtime root and is not an
    absolute path, so nothing is probed beside it and the bare fallback is what
    is named. That is the same answer on every machine (rule 11b).
    """
    clock = FakeClock()
    runner = tools()
    ctx = transcribe_job(conn, video, clock)

    handler_for(storage_root, runner)(ctx)

    download = runner.download_argv
    assert download[download.index("--js-runtimes") + 1] == FALLBACK_RUNTIME
    assert checkpoint(conn, ctx.job_id)["js_runtime"] == FALLBACK_RUNTIME


def test_a_resolved_runtime_is_passed_on_and_written_down(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """The deno of the private venv reaches the download, and the run records it."""
    clock = FakeClock()
    runner = tools()
    ctx = transcribe_job(conn, video, clock)
    runtime = JavaScriptRuntime(name="deno", path="/opt/venv/bin/deno")

    handler_for(storage_root, runner, javascript=runtime)(ctx)

    download = runner.download_argv
    assert download[download.index("--js-runtimes") + 1] == "deno:/opt/venv/bin/deno"
    assert checkpoint(conn, ctx.job_id)["js_runtime"] == "deno:/opt/venv/bin/deno"


def test_the_provenance_of_the_transcript_is_recorded_beside_it(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.5: which tool, which version, which model, which audio."""
    clock = FakeClock()
    runner = tools()
    ctx = transcribe_job(conn, video, clock)

    handler_for(storage_root, runner)(ctx)

    row = transcript_rows(conn, video)[0]
    meta = artifact_meta(conn, storage_root, row["artifact_id"])
    assert meta["origin"] == LOCAL_SPEECH_TO_TEXT
    assert meta["video_id"] == PLATFORM_VIDEO_ID
    provenance = meta["provenance"]
    assert provenance["tool"] == "textflowkit"
    assert provenance["tool_version"] == "0.1.6"
    assert provenance["model"] == "small"
    # The device is stated by the transcript file alone: no setting carries it.
    assert provenance["device"] == "cpu"
    assert provenance["audio_sha256"] == hashlib.sha256(AUDIO_BYTES).hexdigest()
    assert provenance["origin"] == LOCAL_SPEECH_TO_TEXT


def test_the_model_the_transcript_states_wins_over_the_setting(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """What ran is a fact the tool wrote down, so the file is believed."""
    clock = FakeClock()
    runner = tools(document=transcript_document(metadata={"model": "medium", "device": "cuda"}))
    ctx = transcribe_job(conn, video, clock)

    handler_for(storage_root, runner, settings=TranscribeSettings(model="small"))(ctx)

    row = transcript_rows(conn, video)[0]
    provenance = artifact_meta(conn, storage_root, row["artifact_id"])["provenance"]
    assert provenance["model"] == "medium"
    assert provenance["device"] == "cuda"


def test_the_transcript_json_is_stored_as_an_artifact_but_the_audio_is_not(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.6 stores the small files a record is built from, not the media."""
    clock = FakeClock()
    runner = tools()
    ctx = transcribe_job(conn, video, clock)

    handler_for(storage_root, runner)(ctx)

    kinds = [
        row["kind"] for row in conn.execute("SELECT kind FROM artifacts ORDER BY id").fetchall()
    ]
    assert kinds == [artifacts.TRANSCRIPT]
    stored = artifacts.get(conn, transcript_rows(conn, video)[0]["artifact_id"], storage_root)
    assert stored is not None
    assert stored.rel_path.endswith(".json")
    assert json.loads(stored.path.read_text(encoding="utf-8"))["segments"]
    # The audio is in the work folder and in no table of the store.
    assert (storage_root / "work" / PLATFORM_VIDEO_ID / "audio").is_dir()


# -- the one insert function (spec 8.5: no second code path) ---------------


def test_the_audio_path_inserts_through_the_one_shared_function(
    monkeypatch: pytest.MonkeyPatch,
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
) -> None:
    """Both paths call `store.insert_transcript_with_segments`, and only it."""
    # The claim first, so the spy below cannot make a false one true.
    one = store.insert_transcript_with_segments
    assert transcribe_module.insert_transcript_with_segments is one
    assert job.insert_transcript_with_segments is one
    assert fallback.insert_transcript_with_segments is one

    seen: list[dict[str, Any]] = []

    def spy(conn: sqlite3.Connection, **kwargs: Any) -> int:
        seen.append(kwargs)
        return store.insert_transcript_with_segments(conn, **kwargs)

    # Every name is patched, so a run that reached the second code path would
    # show up as a missing call rather than as a silent pass.
    for module in (transcribe_module, job, fallback):
        monkeypatch.setattr(module, "insert_transcript_with_segments", spy)

    clock = FakeClock()
    runner = tools()
    ctx = transcribe_job(conn, video, clock)
    handler_for(storage_root, runner)(ctx)

    assert len(seen) == 1
    call = seen[0]
    assert call["video_id"] == video
    assert call["origin"] == LOCAL_SPEECH_TO_TEXT
    assert isinstance(call["provenance"], Provenance)
    assert call["provenance"].origin == LOCAL_SPEECH_TO_TEXT
    assert call["provenance"].audio_sha256 == hashlib.sha256(AUDIO_BYTES).hexdigest()
    assert len(call["segments"]) == 2
    # The row went in: the spy called through rather than replacing the write.
    assert len(transcript_rows(conn, video)) == 1


def test_the_same_audio_transcribed_again_adds_no_second_transcript(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.6, 8.7: the same bytes are the same artifact, so the same transcript."""
    clock = FakeClock()
    first_runner = tools()
    first_ctx = transcribe_job(conn, video, clock)
    handler_for(storage_root, first_runner)(first_ctx)
    done(conn, first_ctx, clock)
    first = transcript_rows(conn, video)[0]

    second_runner = tools()
    handler_for(storage_root, second_runner)(transcribe_job(conn, video, clock))

    rows = transcript_rows(conn, video)
    assert len(rows) == 1
    assert rows[0]["id"] == first["id"]
    assert len(lines_of(conn, first["id"])) == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM artifacts").fetchone()["n"] == 1


# -- the failures of a transcription ---------------------------------------


def test_a_transcriber_the_runtime_cannot_answer_for_pauses_the_job(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """A missing transcriber is a setup to finish, not a download to waste."""
    clock = FakeClock()
    runner = tools()
    runner.textflowkit.version_rc = 1
    runner.textflowkit.stderr = "textflowkit: not found"
    ctx = transcribe_job(conn, video, clock)

    with pytest.raises(JobPaused) as raised:
        handler_for(storage_root, runner)(ctx)

    assert "TextFlowKit runtime" in str(raised.value)
    assert runner.ytdlp.calls == []


def test_a_transcriber_that_answers_no_version_pauses_the_job(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """A version nobody can record is a provenance that cannot be written."""
    clock = FakeClock()
    runner = tools()
    runner.textflowkit.version_stdout = ""
    ctx = transcribe_job(conn, video, clock)

    with pytest.raises(JobPaused):
        handler_for(storage_root, runner)(ctx)
    assert runner.ytdlp.calls == []


def test_a_rate_limited_download_defers_and_still_names_the_reason(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 8.3 reaches here too: a 429 is a retry, and the reason is kept."""
    clock = FakeClock()
    runner = tools(returncode=1, stderr=RATE_LIMIT_STDERR)
    ctx = transcribe_job(conn, video, clock)

    with pytest.raises(JobDeferred):
        handler_for(storage_root, runner)(ctx)

    recorded = checkpoint(conn, ctx.job_id)
    assert recorded["audio_trigger_reason"] == "no_captions"
    assert recorded["reason"] == HANDED_OFF
    assert transcript_rows(conn, video) == []


def test_a_download_that_writes_no_audio_fails_with_a_reason(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """The command exited zero and wrote nothing: that is a failure, not silence."""
    clock = FakeClock()
    runner = tools()
    runner.ytdlp.returncode = 1
    runner.ytdlp.stderr = "ERROR: unable to download video data\n"
    ctx = transcribe_job(conn, video, clock)

    with pytest.raises(CaptureFailed):
        handler_for(storage_root, runner)(ctx)
    assert transcript_rows(conn, video) == []


def test_a_recording_the_transcriber_heard_nothing_in_fails_with_a_reason(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """There is nothing after the audio fallback, so an empty transcript is a failure."""
    clock = FakeClock()
    runner = tools(document=transcript_document(segments=[]))
    ctx = transcribe_job(conn, video, clock)

    with pytest.raises(CaptureFailed) as raised:
        handler_for(storage_root, runner)(ctx)

    assert NO_SPEECH in str(raised.value)
    assert transcript_rows(conn, video) == []
    assert conn.execute("SELECT COUNT(*) AS n FROM artifacts").fetchone()["n"] == 0


def test_a_run_that_writes_no_transcript_file_fails_with_a_reason(
    conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """A tool that exits zero and writes nothing has still not transcribed anything."""
    clock = FakeClock()
    runner = tools()
    runner.textflowkit.write_json = False
    ctx = transcribe_job(conn, video, clock)

    with pytest.raises(CaptureFailed) as raised:
        handler_for(storage_root, runner)(ctx)

    assert NO_TRANSCRIPT_FILE in str(raised.value)


def test_the_version_line_is_read_as_the_version_alone() -> None:
    """The tool prints its own name first, and the provenance wants the rest."""
    assert version_of_line("textflowkit 0.1.6") == "0.1.6"
    assert version_of_line("TextFlowKit 0.1.6\n") == "0.1.6"
    assert version_of_line("0.2.0") == "0.2.0"


# -- through the real runner (spec 16.1) -----------------------------------


def test_a_transcription_runs_through_the_real_runner(
    db_path: Path, conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """The job is enqueued, the runner claims it, and the row it wrote is read back."""
    registry = Registry()
    runner_tools = tools()
    register_transcribe(
        storage_root=storage_root,
        registry=registry,
        runner=runner_tools,
        interpreter=str(YTDLP_PYTHON),
        textflowkit_program=str(TEXTFLOWKIT_EXE),
    )
    runner = Runner(db_path, registry=registry)
    # Enqueued through the same registry the handler was registered in, which
    # is how a caller finds out which lane this kind belongs on.
    job_id = enqueue(
        conn,
        JOB_KIND,
        payload_for(video, AudioTrigger.CAPTIONS_UNUSABLE, HANDED_OFF),
        registry=registry,
    )

    assert get(conn, job_id)["lane"] == TRANSCRIBE_LANE
    assert runner.run_once(TRANSCRIBE_LANE) == job_id
    row = get(conn, job_id)
    assert row["state"] == DONE
    assert row["last_error"] is None
    assert row["lane"] == TRANSCRIBE_LANE
    transcript = transcript_rows(conn, video)[0]
    assert transcript["origin"] == LOCAL_SPEECH_TO_TEXT
    assert len(lines_of(conn, transcript["id"])) == 2


def test_two_transcriptions_never_run_at_once_through_the_real_runner(
    db_path: Path, conn: sqlite3.Connection, storage_root: Path, video: int
) -> None:
    """Spec 16.1: the per-kind limit of one, with the real handler registered.

    The two jobs are on *different* lanes, one heavy and one normal, so a peak
    of one can only come from the limit on the kind: the lanes alone would
    allow three at a time. The default runner settings are used, because the
    limit is the one the settings already carry for this kind.
    """
    live = 0
    peak = 0
    lock = threading.Lock()
    runner_tools = tools()
    inner = handler_for(storage_root, runner_tools)

    def counted(ctx: JobContext) -> None:
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.15)
        try:
            inner(ctx)
        finally:
            with lock:
                live -= 1

    registry = Registry()
    registry.register(TRANSCRIBE_JOB_KIND, counted, lane=TRANSCRIBE_LANE)
    runner = Runner(db_path, registry=registry)
    ids = [
        enqueue(
            conn,
            JOB_KIND,
            payload_for(video, AudioTrigger.NO_CAPTIONS, HANDED_OFF),
            lane="heavy",
        ),
        enqueue(
            conn,
            JOB_KIND,
            payload_for(video, AudioTrigger.CAPTIONS_UNUSABLE, HANDED_OFF),
            lane="normal",
        ),
    ]

    runner.start()
    try:
        assert wait_for(lambda: all(get(conn, job)["state"] == DONE for job in ids), timeout=15.0)
    finally:
        runner.stop(timeout=5.0)

    assert peak == 1
    assert [get(conn, job)["state"] for job in ids] == [DONE, DONE]
    assert len(transcript_rows(conn, video)) == 1


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Wait for a condition another thread brings about."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
