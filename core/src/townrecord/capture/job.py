"""The caption capture job (spec 8.2, 8.3, 8.6, 8.7).

One job, one video, and the order the spec gives:

1. readiness (8.2): an upcoming or live video waits, an unknown one waits for
   its metadata, and neither is a failure;
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
   marked captured, and the same bytes again change nothing (8.7).

The storage root is a setting the user chooses (spec 8.6), and the settings
screen that saves it is a later unit, so the job takes the root as an argument
and is wired with it. Nothing here reads a root from a default.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import artifacts, proc, repo
from ..captions.parse import Segment, parse_srv3, parse_vtt
from ..jobs import JobContext
from . import archive, command, gate, limits, sidecar, work
from .command import CaptureFailed
from .settings import CaptureSettings

logger = logging.getLogger(__name__)

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


def insert_transcript_with_segments(
    conn: sqlite3.Connection,
    *,
    video_id: int,
    artifact_id: int,
    origin: str,
    segments: Sequence[Segment],
) -> int:
    """Store a transcript and its lines in one transaction, and return its id.

    Half a transcript is worse than none: a row whose lines are missing reads
    like a meeting where nobody spoke. So the two writes are one transaction
    and either both land or neither does (spec 8.7, PROJECT-BRIEF rule F).

    The capture is provisional: the 24 hour rule of spec 8.7 is what makes a
    capture final, and the pass that rechecks it is a later unit.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        transcript_id = repo.insert_transcript(
            conn, video_id=video_id, artifact_id=artifact_id, origin=origin, is_provisional=True
        )
        for segment in segments:
            repo.insert_segment(
                conn,
                transcript_id=transcript_id,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.text,
                speaker_label=None,
            )
        conn.execute("COMMIT")
    except BaseException:
        with suppress(sqlite3.Error):  # nothing to roll back
            conn.execute("ROLLBACK")
        raise
    return transcript_id


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

    def __post_init__(self) -> None:
        self.storage_root = Path(self.storage_root)
        if self.runner is None:
            self.runner = proc.run
        if self.interpreter is None:
            self.interpreter = sys.executable

    def __call__(self, ctx: JobContext) -> None:
        video = self._video(ctx)
        if self._wait_if_not_ready(ctx, video):
            return
        folder = work.work_dir(self.storage_root, video.platform_video_id)
        result = self._run(ctx, video, folder)
        if not result.ok:
            self._handle_failure(ctx, result)
        self._store(ctx, video, folder)
        repo.set_capture_state(ctx.conn, video.id, "captions")
        logger.info("Captured the captions of video %s (%s).", video.id, video.platform_video_id)

    # -- steps -------------------------------------------------------------

    def _video(self, ctx: JobContext) -> repo.Video:
        video_id = video_id_from(ctx.payload)
        video = repo.get_video(ctx.conn, video_id)
        if video is None:
            raise CaptureFailed(f"There is no video with id {video_id}.")
        return video

    def _wait_if_not_ready(self, ctx: JobContext, video: repo.Video) -> bool:
        """Send the job back to the queue when the video is not ready (spec 8.2)."""
        reason = gate.wait_reason(video.readiness)
        if reason is None:
            return False
        if gate.marks_skipped(video.readiness):
            repo.set_capture_state(ctx.conn, video.id, "skipped")
        logger.info("Video %s is not ready: %s", video.id, reason)
        ctx.defer(reason, delay_s=self.settings.not_ready_retry_s)
        return True  # not reached: defer raises

    def _run(self, ctx: JobContext, video: repo.Video, folder: Path):
        """Write the archive and run the spec 8.3 command."""
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
        )
        logger.info(
            "Capturing video %s into %s (archive holds %s lines, resume=%s).",
            video.id,
            folder,
            written,
            resume,
        )
        ctx.heartbeat()
        return command.run_capture(self.runner, argv, timeout_s=self.settings.process_timeout_s)

    def _handle_failure(self, ctx: JobContext, result: proc.ProcessResult) -> None:
        """Decide what a non-zero exit means. Never returns normally."""
        marker = command.rate_limit_marker(result.stderr)
        if marker is not None:
            # Spec 8.3: "HTTP 429 means 'rate limited.' It is a paced retry on a
            # later pass. It is NOT a reason to download audio." So the job is
            # queued again with a delay, and no other command is ever built.
            logger.warning("Video capture was rate limited (%s).", marker)
            ctx.defer(
                f"rate limited by YouTube ({marker}), will retry later",
                delay_s=self.settings.rate_limit_retry_s,
            )
        last = result.last_stderr_line()
        raise CaptureFailed(
            f"yt-dlp exited with {result.returncode}"
            + (f": {last}" if last else " and said nothing.")
        )

    def _store(self, ctx: JobContext, video: repo.Video, folder: Path) -> None:
        """Store the artifacts, the transcript and the lines (spec 8.6, 8.7)."""
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
            raise CaptureFailed(NO_CAPTION_FILE)
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
        caption_artifact = artifacts.store(
            ctx.conn,
            self.storage_root,
            artifacts.TRANSCRIPT,
            caption_path.read_bytes(),
            caption_path.suffix.lstrip("."),
            meta={"video_id": video.platform_video_id, "origin": origin},
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
            return
        segments = parse_caption(caption_path)
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
