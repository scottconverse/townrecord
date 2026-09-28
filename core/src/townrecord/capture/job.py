"""The caption capture job (spec 8.2, 8.3, 8.6, 8.7).

One job, one video, and the order the spec gives:

1. readiness (8.2): an upcoming or live video waits, an unknown one is asked
   what its status is -- the API, the player endpoint, then yt-dlp, in the
   spec's order -- and a row that is still unknown after being asked waits
   with the sentence naming what was asked and what each step answered.
   None of the three is a failure;
2. the download archive is written from the database, so a stale file cannot
   hide a meeting (8.7);
3. the command of 8.3 runs, as an argument list, with an allow-listed
   environment and never through a shell;
4. HTTP 429 is a paced retry on a later pass, never a reason to grab audio;
5. the size and length limits are checked, and a capture over one of them is
   refused with its reason;
6. the caption file and the sidecar are stored as artifacts, and a missing
   sidecar is recorded rather than passed off as a success (8.6);
7. the transcript and its lines are written in one transaction, the video is
   marked captured, and the same bytes again change nothing (8.7);
8. a run that ends with no usable captions is handed to the fallbacks of
   spec 8.4 -- the sister channel, then the audio -- and is never a silent
   success (the "Show transcript" panel of 8.4.2 is not implemented yet).

A bot check is the one thing that is answered inside a single run: when YouTube
says "Sign in to confirm you're not a bot", the command runs again once with
each player client YouTube serves a signed-out reader
(:data:`~townrecord.capture.command.PLAYER_CLIENTS`), and the first client that
answers is the one the capture records. A run every client refused is deferred
with a sentence, the way a rate limit is: nothing here signs in and nothing uses
browser cookies (spec 8.10), and audio is not the answer.

The JavaScript runtime yt-dlp is told to use comes from the private runtime of
spec 8.9, which installs it beside yt-dlp (spec 8.3): the value is resolved from
the injected interpreter, so the argument list does not depend on what is on the
user's PATH, and the runtime that ran is named in the artifact's own record.

The storage root is a setting the user chooses (spec 8.6), and the settings
screen that saves it is a later unit, so the job takes the root as an argument
and is wired with it. Nothing here reads a root from a default.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import artifacts, pacing, proc, repo
from ..captions import CaptionParseError
from ..captions.parse import Segment, parse_srv3, parse_vtt
from ..jobs import JobContext
from ..runtime.javascript import JavaScriptRuntime
from ..runtime.javascript import resolve as resolve_javascript
from ..stt.audio import AudioTrigger
from ..video.youtube.readiness import READINESS_UNKNOWN, READINESS_WAITING_REASON
from ..video.youtube.status import StatusAsker
from . import archive, command, fallback, gate, limits, sidecar, work
from .command import CaptureFailed
from .settings import CaptureSettings
from .store import insert_transcript_with_segments

logger = logging.getLogger(__name__)

#: What a caption capture is called in the process pace's own log line
#: (:mod:`townrecord.pacing`), so a run that is waiting says what it waits for.
PACE_WHAT = "a caption capture"
#: The reason recorded when yt-dlp finished but wrote no sidecar.
MISSING_SIDECAR_REASON = (
    "yt-dlp finished without writing an info.json sidecar, so nothing says who made "
    "the captions or how long the video is."
)

#: The reason recorded when the sidecar names no English track.
NO_ENGLISH_TRACK = (
    "The info.json names no English caption track, so TownRecord cannot say whether "
    "the captions were the publisher's or YouTube's."
)

#: The reason recorded when a run wrote no caption file at all.
NO_CAPTION_FILE = "yt-dlp finished without writing an English caption file."

#: The capture state a stored caption leaves behind, from ``repo.CAPTURE_STATES``.
#: ``repo.set_capture_state`` refuses a word that is not one of the five, so a
#: typo here fails loudly instead of writing a state nothing looks for.
CAPTIONS_STATE = "captions"

#: The reason recorded when "Local transcription only" (spec 8.10) skipped the
#: caption command: the video is transcribed instead, and the reason says so.
LOCAL_TRANSCRIPTION_ONLY_REASON = (
    "Local transcription only is on for this machine, so no caption track was fetched."
)

#: The sentence a capture defers with when YouTube asked the machine to prove it
#: is not a robot and every player client of spec 8.3's ladder refused too. It is
#: a wait, like a rate limit, and never a failure: nothing here signs in, and
#: spec 8.10 is why.
BOT_CHECK_DEFERRAL = (
    "YouTube asked this machine to prove it is not a robot. The clients it serves a "
    "signed-out reader ({tried}) all refused, and TownRecord neither signs in nor uses "
    "browser cookies, so the capture waits and tries again later."
)


@dataclass(frozen=True)
class Attempt:
    """One run of the caption command, and the player client it named.

    Spec 8.3's ladder makes several runs of nearly the same command, and what
    is stored has to say which one answered, so the result is never separated
    from the client that produced it.
    """

    result: proc.ProcessResult
    #: The YouTube player client this run named, or "" for the plain command
    #: that named none and got whatever YouTube serves by default.
    player_client: str = ""


def video_id_from(payload: Any) -> int:
    """Return the `videos.id` the job was enqueued for.

    The payload is the video id itself, which is how the queue is enqueued. A
    mapping with a ``video_id`` key is accepted too, so a caller that wants to
    carry more than the id is not forced to change this job.
    """
    value = payload.get("video_id") if isinstance(payload, Mapping) else payload
    if isinstance(value, bool):
        raise CaptureFailed("The job payload is not a video id.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CaptureFailed(f"The job payload {value!r} is not a video id.") from exc


def parse_caption(path: Path) -> list[Segment]:
    """Parse a caption file into timed segments: srv3, else vtt (spec 8.3).

    The file that is parsed is the file that was stored, so the segments always
    belong to the artifact the transcript points at.
    """
    data = path.read_bytes()
    if path.suffix == ".srv3":
        return parse_srv3(data)
    return parse_vtt(data)


@dataclass
class CaptionCapture:
    """The handler for the `capture_captions` job kind."""

    #: The absolute storage root the user chose (spec 8.6).
    storage_root: Path
    settings: CaptureSettings = field(default_factory=CaptureSettings)
    #: How the command is run. Tests pass a fake that writes the files yt-dlp
    #: would have written, so no test ever reaches YouTube. None means
    #: `proc.run`, which runs the real child.
    runner: command.Runner | None = None
    #: The interpreter that runs `-m yt_dlp` (spec 8.9 gives the tools their
    #: own runtime; the path is a later unit's concern, so it is injected).
    #: None means this process's interpreter.
    interpreter: str | None = None
    #: The JavaScript runtime yt-dlp is told to use (spec 8.3, 8.9). None means
    #: it is resolved from the interpreter above: the deno the private runtime
    #: installed beside yt-dlp when it is there, and the bare fallback name when
    #: it is not.
    javascript: JavaScriptRuntime | None = None
    #: How a row that says ``unknown`` is asked what the video's status is
    #: (spec 8.2, :mod:`townrecord.video.youtube.status`). None means one is
    #: built from the runner, the interpreter and the JavaScript runtime above,
    #: so an injected fake runner is the fake this asks through too and no test
    #: starts a process.
    status_asker: StatusAsker | None = None

    def __post_init__(self) -> None:
        self.storage_root = Path(self.storage_root)
        if self.runner is None:
            self.runner = proc.run
        if self.interpreter is None:
            self.interpreter = sys.executable
        if self.javascript is None:
            self.javascript = resolve_javascript(self.interpreter)
        if self.status_asker is None:
            self.status_asker = StatusAsker(
                runner=self.runner,
                interpreter=self.interpreter,
                js_runtime=self.javascript.argument,
            )

    def __call__(self, ctx: JobContext) -> None:
        video = self._video(ctx)
        if self._wait_if_not_ready(ctx, video):
            return
        if self.settings.local_transcription_only:
            self._transcribe_instead(ctx, video)
            return
        folder = work.work_dir(self.storage_root, video.platform_video_id)
        attempt = self._run(ctx, video, folder)
        if not attempt.result.ok:
            self._handle_failure(ctx, attempt)
        state = self._store(ctx, video, folder, attempt)
        if state is not None:
            repo.set_capture_state(ctx.conn, video.id, state)
        logger.info(
            "Video %s (%s) finished as %s.",
            video.id,
            video.platform_video_id,
            state if state is not None else "a hand-off (spec 8.4)",
        )

    def _transcribe_instead(self, ctx: JobContext, video: repo.Video) -> None:
        """Skip the caption command entirely (spec 8.10).

        "Local transcription only" is a setting, not a failure: nothing is run
        and nothing is downloaded from the caption path, and the video goes
        straight to the audio fallback of spec 8.4.3. The reason is recorded,
        so the video does not read as a capture that quietly did nothing.
        """
        handoff = fallback.enqueue_transcription(
            ctx,
            video,
            trigger=AudioTrigger.LOCAL_TRANSCRIPTION_ONLY,
            reason=LOCAL_TRANSCRIPTION_ONLY_REASON,
        )
        logger.info(
            "Video %s: local transcription only is on, so transcription job %s was queued.",
            video.id,
            handoff.job_id,
        )

    # -- steps -------------------------------------------------------------

    def _video(self, ctx: JobContext) -> repo.Video:
        video_id = video_id_from(ctx.payload)
        video = repo.get_video(ctx.conn, video_id)
        if video is None:
            raise CaptureFailed(f"There is no video with id {video_id}.")
        return video

    def _wait_if_not_ready(self, ctx: JobContext, video: repo.Video) -> bool:
        """Find out whether the video is ready, then wait or carry on (spec 8.2).

        A listing writes the readiness it managed to read, and a public feed
        carries no status at all, so a row can sit at ``unknown`` with nothing
        in the system that would ever change it. Reading that word back and
        deferring on it would wait forever, so the word ``unknown`` is the one
        that means "ask": spec 8.2's order, the API then the player endpoint
        then yt-dlp, is asked by :class:`~townrecord.video.youtube.status.StatusAsker`.

        The answer is written on the row before anything else happens, so the
        next run reads it instead of asking again, and this run continues or
        defers with the reason the answer actually gives. An answer that is
        still ``unknown`` defers too, and its sentence says which steps were
        asked and what each of them said, so a video waiting for its status is
        never a bare "unknown".
        """
        word = video.readiness
        note = ""
        if gate.wait_reason(word) == READINESS_WAITING_REASON:
            # The row has no usable status, so this is the run that asks
            # (spec 8.2). An "upcoming" or "live" row is a definite word and
            # is not asked again: it is simply skipped and retried.
            ctx.heartbeat()
            answer = self.status_asker.ask(
                video_id=video.platform_video_id,
                url=command.watch_url(video.url, video.platform_video_id),
            )
            note = f" {answer.sentence}"
            if answer.readiness != READINESS_UNKNOWN:
                repo.set_readiness(ctx.conn, video.id, answer.readiness)
                word = answer.readiness
        reason = gate.wait_reason(word)
        if reason is None:
            return False
        if gate.marks_skipped(word):
            repo.set_capture_state(ctx.conn, video.id, "skipped")
        logger.info("Video %s is not ready: %s%s", video.id, reason, note)
        deferral = reason if not note else f"{reason}.{note}"
        ctx.defer(deferral, delay_s=self.settings.not_ready_retry_s)
        return True  # not reached: defer raises

    def _run(self, ctx: JobContext, video: repo.Video, folder: Path) -> Attempt:
        """Write the archive and run the spec 8.3 command, ladder included."""
        # Spec 8.7: the archive is regenerated from the database before every
        # capture, so a stale file on disk cannot keep a meeting out of it.
        archive_file, written = archive.write(ctx.conn, self.storage_root)
        resume = work.has_capture(folder)  # read before the folder is created
        folder.mkdir(parents=True, exist_ok=True)
        argv = command.capture_argv(
            interpreter=self.interpreter,
            work_dir=folder,
            archive=archive_file,
            url=command.watch_url(video.url, video.platform_video_id),
            resume=resume,
            js_runtime=self.javascript.argument,
        )
        logger.info(
            "Capturing video %s into %s (archive holds %s lines, resume=%s, js=%s).",
            video.id,
            folder,
            written,
            resume,
            self.javascript.argument,
        )
        return self._with_player_clients(ctx, video, argv)

    def _with_player_clients(
        self, ctx: JobContext, video: repo.Video, argv: Sequence[str]
    ) -> Attempt:
        """Run the command, then one retry per player client after a bot check.

        Only a bot check goes round the ladder. A rate limit, any other failure
        and a run that worked all end it, because spec 8.3 asks for one retry
        per client and no more: the first client that answers is the answer.
        """
        attempt = self._attempt(ctx, argv)
        if not self._needs_another_client(attempt.result):
            return attempt
        for client in command.PLAYER_CLIENTS:
            logger.warning(
                "Video %s: YouTube asked for a bot check; retrying with the %s client.",
                video.id,
                client,
            )
            attempt = self._attempt(ctx, command.with_player_client(argv, client), client)
            if not self._needs_another_client(attempt.result):
                return attempt
        return attempt

    def _attempt(self, ctx: JobContext, argv: Sequence[str], client: str = "") -> Attempt:
        """Run one command and keep it together with the client it named.

        The command is a YouTube request, so it waits for the process pace
        first (spec 8.10). The wait is here, in the one place every command of
        the spec 8.3 ladder goes through, so a ladder of four commands is four
        waits rather than one: spacing only the first would still leave the
        three retries back to back, which is the burst that was refused.
        """
        pacing.pacer().wait(what=PACE_WHAT)
        ctx.heartbeat()
        result = command.run_capture(self.runner, argv, timeout_s=self.settings.process_timeout_s)
        return Attempt(result=result, player_client=client)

    @staticmethod
    def _needs_another_client(result: proc.ProcessResult) -> bool:
        """True when this run failed on a bot check and nothing else."""
        return not result.ok and command.bot_check_marker(result.stderr) is not None

    def _handle_failure(self, ctx: JobContext, attempt: Attempt) -> None:
        """Decide what a non-zero exit means. Never returns normally."""
        result = attempt.result
        marker = command.rate_limit_marker(result.stderr)
        if marker is not None:
            # Spec 8.3: "HTTP 429 means 'rate limited.' It is a paced retry on a
            # later pass. It is NOT a reason to download audio." So the job is
            # queued again with a delay, and no other command is ever built.
            logger.warning("Video capture was rate limited (%s).", marker)
            # Spec 8.10: a 429 is YouTube answering for the address, so the
            # hold is written to the process pace and every other YouTube job
            # waits with this one. The job's own deferral below is the second
            # half of the same wait, and the pace never shortens either.
            pacing.pacer().rate_limited(
                what=PACE_WHAT, marker=marker, delay_s=self.settings.rate_limit_retry_s
            )
            ctx.defer(
                f"rate limited by YouTube ({marker}), will retry later",
                delay_s=self.settings.rate_limit_retry_s,
            )
        if command.bot_check_marker(result.stderr) is not None:
            # Spec 8.10: no login and no browser cookies, so a bot check is not
            # solved and not a failure either. Every client was tried, the run
            # waits, and the audio fallback of spec 8.4 is not started by it.
            logger.warning(
                "Video capture was refused by every player client (%s).",
                ", ".join(command.PLAYER_CLIENTS),
            )
            ctx.defer(
                BOT_CHECK_DEFERRAL.format(tried=", ".join(command.PLAYER_CLIENTS)),
                delay_s=self.settings.rate_limit_retry_s,
            )
        last = result.last_stderr_line()
        raise CaptureFailed(
            f"yt-dlp exited with {result.returncode}"
            + (f": {last}" if last else " and said nothing.")
        )

    def _store(
        self, ctx: JobContext, video: repo.Video, folder: Path, attempt: Attempt
    ) -> str | None:
        """Store the artifacts, the transcript and the lines (spec 8.6, 8.7).

        Returns:
            The capture state to leave the video in, or None when the captions
            were not stored and a spec 8.4 hand-off took over. A hand-off
            records its own reason and moves the capture state itself, so the
            caller writes nothing twice.
        """
        info_path = work.info_file(folder)
        if info_path is None:
            artifacts.record_missing_sidecar(
                ctx.conn, video.platform_video_id, MISSING_SIDECAR_REASON
            )
            raise CaptureFailed(
                f"Video {video.platform_video_id} was captured without an info.json "
                f"sidecar, and the missing sidecar was recorded."
            )
        caption_path = work.caption_file(folder)
        if caption_path is None:
            # Spec 8.4: a run that wrote no caption file is the first fallback
            # trigger, ``no_captions``. It is never reported as a capture that
            # succeeded, and it is not an error either.
            return self._hand_off(
                ctx, video, reason=NO_CAPTION_FILE, trigger=AudioTrigger.NO_CAPTIONS
            )
        info = work.read_info(info_path)
        origin = sidecar.caption_origin(info)
        if origin is None:
            raise CaptureFailed(NO_ENGLISH_TRACK)
        # Spec 8.3: a refusal is recorded with its reason, and the confirmation
        # that lifts it is a later unit. A refusal is not a failure, so the job
        # pauses with the sentence instead of failing.
        refusal = limits.refusal(
            duration_s=sidecar.duration_s(info),
            capture_bytes=limits.folder_bytes(folder),
            settings=self.settings,
        )
        if refusal is not None:
            ctx.pause(refusal)
        # The file is parsed before it is stored: a caption file that holds no
        # lines is not worth keeping as the artifact of a transcript, and the
        # spec 8.4 fallback starts from what it actually held.
        try:
            segments = parse_caption(caption_path)
        except CaptionParseError as exc:
            return self._hand_off(
                ctx,
                video,
                reason=f"The caption file {caption_path.name} could not be read: {exc}",
                trigger=AudioTrigger.CAPTIONS_UNUSABLE,
            )
        if not segments:
            return self._hand_off(
                ctx,
                video,
                reason=(
                    f"The caption file {caption_path.name} "
                    f"({caption_path.stat().st_size} bytes) parsed to no caption lines."
                ),
                trigger=AudioTrigger.CAPTIONS_UNUSABLE,
            )
        caption_artifact = artifacts.store(
            ctx.conn,
            self.storage_root,
            artifacts.TRANSCRIPT,
            caption_path.read_bytes(),
            caption_path.suffix.lstrip("."),
            meta={
                "video_id": video.platform_video_id,
                "origin": origin,
                # Spec 8.3, 8.9: which JavaScript runtime ran, and which player
                # client answered, so a transcript that later downloads fine on
                # another machine can be compared against what this one used.
                "js_runtime": self.javascript.argument,
                "player_client": attempt.player_client or command.DEFAULT_CLIENT,
            },
        )
        info_artifact = artifacts.store(
            ctx.conn,
            self.storage_root,
            artifacts.INFO,
            info_path.read_bytes(),
            "json",
            meta={"video_id": video.platform_video_id},
        )
        known = repo.transcript_for_artifact(ctx.conn, video.id, caption_artifact.id)
        if known is not None:
            # The same bytes are already the transcript of this video, so this
            # run adds nothing: same artifact, same transcript, same lines.
            logger.info("Video %s already has this transcript (%s).", video.id, known.id)
            return CAPTIONS_STATE
        transcript_id = insert_transcript_with_segments(
            ctx.conn,
            video_id=video.id,
            artifact_id=caption_artifact.id,
            origin=origin,
            segments=segments,
        )
        logger.info(
            "Stored transcript %s of video %s: %s lines, origin %s, sidecar artifact %s.",
            transcript_id,
            video.id,
            len(segments),
            origin,
            info_artifact.id,
        )
        return CAPTIONS_STATE

    def _hand_off(
        self,
        ctx: JobContext,
        video: repo.Video,
        *,
        reason: str,
        trigger: AudioTrigger,
    ) -> None:
        """Take the next step of spec 8.4. The hand-off records the outcome."""
        handoff = fallback.hand_off_captions(ctx, video, reason=reason, trigger=trigger)
        logger.warning(
            "Video %s: %s (%s, %s)",
            video.id,
            reason,
            trigger.value,
            "the meeting's other channel" if handoff.followed_sister else "a transcription job",
        )
