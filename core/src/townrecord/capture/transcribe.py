"""Transcribing one meeting's audio on this machine (spec 8.4.3, 8.5, 8.9).

This is the last fallback of spec 8.4: the audio is downloaded and TextFlowKit
transcribes it locally, so nothing about the meeting leaves the machine
(spec 8.10). The job does the whole of spec 8.5 in one run:

1. record why the audio is being downloaded, before anything else can fail;
2. ask the private runtime of spec 8.9 for the TextFlowKit it owns, and read
   its version;
3. download the audio with the command of
   :func:`~townrecord.stt.audio.audio_download_argv`, run by the *runtime's*
   interpreter and not by whatever Python happens to run TownRecord;
4. hash the audio file that arrived;
5. run :func:`~townrecord.stt.textflowkit.textflowkit_argv` with the timeout of
   :func:`~townrecord.stt.textflowkit.transcription_timeout_s`, and read the
   JSON file it wrote;
6. store that JSON as a ``transcript`` artifact, insert the transcript with
   origin ``local_speech_to_text`` and the provenance beside it, through the one
   insert function both paths use (spec 8.5: no second code path), and mark the
   video captured from audio.

The audio stays in the work folder of the video and is *not* stored as an
artifact: spec 8.10 keeps captured media on the user's machine, and the
content-addressed store of spec 8.6 is for the small files a meeting's record
is built from.

The job kind is ``transcribe``. That spelling is the one the per-kind limit of
spec 16.1 already names (``DEFAULT_KIND_LIMITS``), so one transcription runs at
a time without a settings change; the lane is ``heavy``, whose limit is one as
well.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import artifacts, proc, repo
from ..captions import CaptionParseError
from ..jobs import JobContext
from ..runtime.javascript import JavaScriptRuntime
from ..runtime.javascript import resolve as resolve_javascript
from ..stt.audio import AudioRequest, AudioTrigger
from ..stt.provenance import LOCAL_SPEECH_TO_TEXT, Provenance
from ..stt.textflowkit import (
    TEXTFLOWKIT_PROGRAM,
    parse_textflowkit_json,
    textflowkit_argv,
    transcription_timeout_s,
)
from . import archive, command, sidecar, work
from .command import CaptureFailed, Runner
from .settings import TranscribeSettings
from .store import insert_transcript_with_segments

logger = logging.getLogger(__name__)

#: The job kind, and the name the per-kind limit of spec 16.1 is written for.
JOB_KIND = "transcribe"

#: The lane. Transcription is the heavy work of spec 16.1 (a model runs on this
#: machine and takes minutes), and the heavy lane runs one job at a time.
LANE = "heavy"

#: The payload key holding the reason, spelled as spec 8.5 spells it.
TRIGGER_KEY = "audio_trigger_reason"

#: The extension of the artifact a local transcription is stored as. Spec 8.6
#: names ``.srv3``, ``.vtt`` and ``.json`` for a transcript artifact.
JSON_EXT = "json"

#: The suffix of the file the transcription writes, and of the artifact.
TRANSCRIPT_SUFFIX = ".json"

#: The reason recorded when the download finished but wrote no audio file.
NO_AUDIO_FILE = "yt-dlp finished without writing an audio file."

#: The reason recorded when the transcriber wrote no transcript file.
NO_TRANSCRIPT_FILE = "TextFlowKit finished without writing a transcript file."

#: The reason recorded when the transcriber heard nothing.
NO_SPEECH = (
    "TextFlowKit transcribed this recording to no lines at all, so there is no transcript to store."
)


def payload_for(video_id: int, trigger: AudioTrigger, reason: str) -> dict[str, Any]:
    """Return the payload of one transcription job (spec 8.5).

    A plain mapping, because that is what a job payload is: it is encoded into
    the ``jobs`` row and read back by a later run, possibly in a later process.
    The reason travels with it, so the job always carries why it exists.
    """
    return {
        "video_id": int(video_id),
        TRIGGER_KEY: AudioTrigger.from_value(trigger).value,
        "reason": " ".join(str(reason).split()),
    }


def trigger_from(payload: Any) -> AudioTrigger:
    """Return the reason this audio is being downloaded (spec 8.5).

    A job that names no reason, or names a word that is not one of the three of
    :class:`~townrecord.stt.audio.AudioTrigger`, is refused: audio is never
    downloaded without a reason from the closed set.
    """
    value = payload.get(TRIGGER_KEY) if isinstance(payload, Mapping) else None
    try:
        return AudioTrigger.from_value(value)
    except ValueError as exc:
        raise CaptureFailed(f"This transcription job carries no reason: {exc}") from exc


def reason_from(payload: Any) -> str:
    """Return the sentence explaining this run, or an empty string."""
    if not isinstance(payload, Mapping):
        return ""
    return " ".join(str(payload.get("reason") or "").split())


def transcript_json_path(stdout: bytes, out_dir: Path) -> Path | None:
    """Return the JSON file a finished transcription wrote, or None.

    TextFlowKit's ``--output-dir`` is a folder of our own, but the *name* of
    the file inside it is not ours to predict: the tool adds a random suffix to
    the name of the recording. So the paths the tool prints are read first and
    used when the file is there; a run that printed nothing usable falls back to
    the newest ``.json`` in the folder, and a run that wrote none answers None.
    """
    text = stdout.decode("utf-8", errors="replace") if stdout else ""
    for line in text.splitlines():
        candidate = line.strip().strip("'\"")
        if not candidate.endswith(TRANSCRIPT_SUFFIX):
            continue
        path = Path(candidate)
        if not path.is_absolute():
            path = out_dir / candidate
        if path.is_file():
            return path
    if not out_dir.is_dir():
        return None
    written = [entry for entry in out_dir.glob(f"*{TRANSCRIPT_SUFFIX}") if entry.is_file()]
    if not written:
        return None
    return max(written, key=lambda entry: entry.stat().st_mtime)


def reported_facts(data: bytes) -> tuple[str | None, str | None]:
    """Return the model and the device the transcription file says it used.

    TextFlowKit writes what it ran with into its own JSON. When the file states
    one of them, that is the fact and it is recorded; when it does not, the
    answer is None and the caller keeps what it asked for (or leaves the device
    unknown, which is not the same as "the CPU").
    """
    try:
        document = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, None
    if not isinstance(document, Mapping):
        return None, None
    stated = document.get("metadata")
    if not isinstance(stated, Mapping):
        return None, None
    return _fact(stated.get("model")), _fact(stated.get("device"))


def _fact(value: Any) -> str | None:
    """Read a stated fact as text. Blank means the file stated none."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def version_of_line(line: str) -> str:
    """Return the version from the line the tool answers ``--version`` with.

    TextFlowKit prints ``textflowkit 0.1.6``; the name is dropped and the rest
    is the version. A tool that prints the version alone keeps it.
    """
    text = " ".join(line.split())
    program = TEXTFLOWKIT_PROGRAM.lower()
    if text.lower().startswith(f"{program} "):
        return text[len(program) + 1 :].strip()
    return text


@dataclass
class TranscribeAudio:
    """The handler for the ``transcribe`` job kind."""

    #: The absolute storage root the user chose (spec 8.6).
    storage_root: Path
    settings: TranscribeSettings = field(default_factory=TranscribeSettings)
    #: How a child is run. Tests pass a fake that writes the files the tools
    #: would have written, so no test reaches YouTube or runs a model. None
    #: means `proc.run`, which runs the real child.
    runner: Runner | None = None
    #: The interpreter that runs `-m yt_dlp`: the private runtime of spec 8.9.
    #: None means this process's interpreter.
    ytdlp_interpreter: str | None = None
    #: The TextFlowKit program to run: the console script of the runtime that
    #: tool owns (spec 8.9). None means the name, found on PATH.
    textflowkit_program: str | None = None
    #: The JavaScript runtime the audio download is told to use (spec 8.3,
    #: 8.9). None means it is resolved from the yt-dlp interpreter above: the
    #: deno the private runtime installed beside yt-dlp when it is there, and
    #: the bare fallback name when it is not.
    javascript: JavaScriptRuntime | None = None

    def __post_init__(self) -> None:
        self.storage_root = Path(self.storage_root)
        if self.runner is None:
            self.runner = proc.run
        if self.ytdlp_interpreter is None:
            self.ytdlp_interpreter = sys.executable
        if self.textflowkit_program is None:
            self.textflowkit_program = TEXTFLOWKIT_PROGRAM
        if self.javascript is None:
            self.javascript = resolve_javascript(self.ytdlp_interpreter)

    def __call__(self, ctx: JobContext) -> None:
        video = self._video(ctx)
        trigger = trigger_from(ctx.payload)
        reason = reason_from(ctx.payload)
        ctx.heartbeat()
        # Spec 8.5: the reason is recorded every time. It is written before the
        # first child starts, so a run that dies later still says why the audio
        # of this meeting was downloaded at all.
        self._save(ctx, {TRIGGER_KEY: trigger.value, "reason": reason})
        audio_folder = work.audio_dir(self.storage_root, video.platform_video_id)
        out_dir = work.transcript_dir(self.storage_root, video.platform_video_id)
        version = self._tool_version(ctx)
        audio_path = self._download(ctx, video, audio_folder)
        digest = artifacts.hash_file(audio_path)
        json_path = self._transcribe(ctx, audio_folder, out_dir, audio_path)
        data = json_path.read_bytes()
        segments = self._segments(json_path, data)
        model, device = reported_facts(data)
        provenance = Provenance(
            tool_version=version,
            model=model or self.settings.model,
            audio_sha256=digest,
            device=device,
        )
        artifact = artifacts.store(
            ctx.conn,
            self.storage_root,
            artifacts.TRANSCRIPT,
            data,
            JSON_EXT,
            meta={"video_id": video.platform_video_id, "origin": LOCAL_SPEECH_TO_TEXT},
        )
        known = repo.transcript_for_artifact(ctx.conn, video.id, artifact.id)
        if known is None:
            transcript_id = insert_transcript_with_segments(
                ctx.conn,
                video_id=video.id,
                artifact_id=artifact.id,
                origin=LOCAL_SPEECH_TO_TEXT,
                segments=segments,
                provenance=provenance,
            )
        else:
            # The same audio transcribed again is the same artifact and so the
            # same transcript (spec 8.6, 8.7): this run adds nothing.
            transcript_id = known.id
            logger.info("Video %s already has this transcription (%s).", video.id, known.id)
        repo.set_capture_state(ctx.conn, video.id, "audio")
        self._save(
            ctx,
            {
                TRIGGER_KEY: trigger.value,
                "reason": reason,
                "audio_sha256": digest,
                "audio_path": audio_path.relative_to(self.storage_root).as_posix(),
                "js_runtime": self.javascript.argument,
                "model": provenance.model,
                "tool_version": provenance.tool_version,
                "device": provenance.device,
                "transcript_id": transcript_id,
                "segments": len(segments),
            },
        )
        logger.info(
            "Transcribed the audio of video %s: %s lines, %s %s, audio %s, transcript %s.",
            video.id,
            len(segments),
            provenance.tool,
            provenance.tool_version,
            digest,
            transcript_id,
        )

    # -- steps -------------------------------------------------------------

    def _video(self, ctx: JobContext) -> repo.Video:
        """Return the video this job was enqueued for."""
        value = ctx.payload.get("video_id") if isinstance(ctx.payload, Mapping) else None
        if isinstance(value, bool):
            raise CaptureFailed("The job payload is not a video id.")
        try:
            video_id = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise CaptureFailed(f"The job payload {value!r} is not a video id.") from exc
        video = repo.get_video(ctx.conn, video_id)
        if video is None:
            raise CaptureFailed(f"There is no video with id {video_id}.")
        return video

    def _save(self, ctx: JobContext, facts: Mapping[str, Any]) -> None:
        """Write what this run has found so far into the job's checkpoint.

        The checkpoint is one value, so the run reads it back first: a job that
        went back into the queue after a crash resumes with what it already
        recorded instead of losing it (spec 16.1).
        """
        before = ctx.checkpoint()
        record: dict[str, Any] = dict(before) if isinstance(before, Mapping) else {}
        record.update(facts)
        ctx.save_checkpoint(record)

    def _run(self, argv: list[str], timeout_s: float) -> proc.ProcessResult:
        """Run one child with the allow-listed environment (spec 8.3, rule E).

        The environment is built by :func:`townrecord.capture.command.run_capture`
        and not here, so both jobs hand a child exactly the same set of names.
        """
        return command.run_capture(self.runner, argv, timeout_s=timeout_s)

    def _tool_version(self, ctx: JobContext) -> str:
        """Ask the TextFlowKit runtime which version it holds (spec 8.9).

        A missing or broken transcriber is not a failed job: it is a setup the
        user has to finish, so the job pauses with the sentence rather than
        burning through a download first.
        """
        program = str(self.textflowkit_program)
        result = self._run([program, "--version"], self.settings.version_timeout_s)
        if not result.ok:
            ctx.pause(
                f"The TextFlowKit runtime would not say which version it holds "
                f"({_tail(result)}). Run setup to install it."
            )
        text = result.stdout.decode("utf-8", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            ctx.pause(
                "The TextFlowKit runtime answered no version, so TownRecord cannot record "
                "which tool made this transcript. Run setup to install it again."
            )
        return version_of_line(lines[0])

    def _download(self, ctx: JobContext, video: repo.Video, folder: Path) -> Path:
        """Download the audio of this meeting into its own work folder (8.5)."""
        # Spec 8.7: the archive is regenerated from the database before every
        # download, so a stale file on disk cannot keep a meeting out of it.
        archive_file, written = archive.write(ctx.conn, self.storage_root)
        folder.mkdir(parents=True, exist_ok=True)
        request = AudioRequest(
            watch_url=command.watch_url(video.url, video.platform_video_id),
            trigger=trigger_from(ctx.payload),
        )
        argv = request.argv(folder, archive_file, js_runtime=self.javascript.argument)
        # Element 0 is the interpreter that runs `-m yt_dlp`: the private
        # runtime of spec 8.9, never whatever is on the user's PATH.
        argv[0] = str(self.ytdlp_interpreter)
        logger.info(
            "Downloading the audio of video %s into %s (archive holds %s lines).",
            video.id,
            folder,
            written,
        )
        ctx.heartbeat()
        result = self._run(argv, self.settings.download_timeout_s)
        if not result.ok:
            marker = command.rate_limit_marker(result.stderr)
            if marker is not None:
                # Spec 8.3: a rate limit is a paced retry on a later pass, and
                # it is never a reason to fetch anything else.
                logger.warning("The audio download was rate limited (%s).", marker)
                ctx.defer(
                    f"rate limited by YouTube ({marker}), will retry later",
                    delay_s=self.settings.rate_limit_retry_s,
                )
            raise CaptureFailed(
                f"yt-dlp exited with {result.returncode}"
                + (f": {_tail(result)}" if _tail(result) else " and said nothing.")
            )
        path = work.audio_file(folder)
        if path is None:
            raise CaptureFailed(NO_AUDIO_FILE)
        return path

    def _transcribe(
        self, ctx: JobContext, audio_folder: Path, out_dir: Path, audio_path: Path
    ) -> Path:
        """Transcribe the audio and return the JSON file that was written."""
        out_dir.mkdir(parents=True, exist_ok=True)
        argv = textflowkit_argv(
            str(audio_path),
            str(out_dir),
            model=self.settings.model,
            language=self.settings.language,
        )
        # Element 0 is the program to run: the console script of the runtime
        # this tool owns (spec 8.9), not a name looked up on PATH.
        argv[0] = str(self.textflowkit_program)
        timeout_s = transcription_timeout_s(_audio_seconds(audio_folder))
        logger.info(
            "Transcribing %s with %s (%s), up to %.0f seconds.",
            audio_path.name,
            self.textflowkit_program,
            self.settings.model,
            timeout_s,
        )
        ctx.heartbeat()
        result = self._run(argv, timeout_s)
        if not result.ok:
            raise CaptureFailed(
                f"TextFlowKit exited with {result.returncode}"
                + (f": {_tail(result)}" if _tail(result) else " and said nothing.")
            )
        path = transcript_json_path(result.stdout, out_dir)
        if path is None:
            raise CaptureFailed(NO_TRANSCRIPT_FILE)
        return path

    def _segments(self, path: Path, data: bytes) -> list[Any]:
        """Read the transcription into the shared segment type (spec 10.5).

        A file that is not a TextFlowKit transcript, and a transcript with no
        lines in it, are both failures with a reason: this is the last fallback
        of spec 8.4, so there is nothing further to hand off to and a stored
        transcript with no lines would read like a meeting where nobody spoke.
        """
        try:
            segments = parse_textflowkit_json(data)
        except CaptionParseError as exc:
            raise CaptureFailed(f"{path.name} is not a usable transcript: {exc}") from exc
        if not segments:
            raise CaptureFailed(f"{NO_SPEECH} ({path.name})")
        return list(segments)


def _audio_seconds(folder: Path) -> float | None:
    """Return the length of the downloaded audio, or None when unknown.

    The length is read from the sidecar the audio download wrote beside it. A
    sidecar that is missing, unreadable or silent about the length means the
    length is not known, which is not the same as zero: the timeout of
    spec 8.5 then uses its own answer for an unknown length.
    """
    path = work.info_file(folder)
    if path is None:
        return None
    try:
        info = work.read_info(path)
    except (OSError, ValueError):
        return None
    return sidecar.duration_s(info)


def _tail(result: proc.ProcessResult) -> str:
    """Return the last plain line a child printed, or an empty string."""
    last = result.last_stderr_line()
    return " ".join(str(last).split()) if last else ""
