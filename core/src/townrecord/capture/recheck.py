"""The caption recheck job (spec 8.7, 16.1).

Spec 8.7 makes a capture provisional and then watches it:

    "A capture is provisional for 24 hours after the meeting ends. Recheck every
    3 hours in that time. Two unchanged checks at least 1 hour apart settle it. A
    capture made more than 24 hours after the meeting is final at once. At 48
    hours after the first capture, settle it anyway; if the transcript was still
    changing, mark it 'settled under churn'."

The arithmetic of those sentences is :mod:`townrecord.capture.settle`, which
reads no database and runs nothing. This module is the part that does: it reads
what the video has, asks the source again, compares, writes the check down, and
either settles the capture or hands the job back to the queue for the next look.

**It never sleeps.** A recheck that is not due yet calls
:meth:`~townrecord.jobs.JobContext.defer` with the moment the next check is due,
which puts the same job back on the queue with ``run_after`` set and frees the
worker at once (spec 16.1). A hold that the process pace of spec 8.10 put there
is handed back the same way, by the pace itself, so a 429 costs a lane nothing
while it lasts.

**The source is read afresh.** The capture's own work folder cannot be reused:
its download archive records that the video was captured, and yt-dlp skips a
video the archive lists, so a recheck pointed at it would read the file left by
the first capture and call it a fresh answer. The recheck therefore fetches into
its own folder (:func:`townrecord.capture.work.fresh_recheck_dir`) with its own
always-empty archive, and wipes that folder before each fetch so a file left by
the previous check cannot be read as the answer to this one.

**A revision adds, and never rewrites.** The new captions are stored as a new
artifact and a new transcript row; the older version and its hash stay readable
(spec 8.6, 8.7). One review item is raised, with a plain sentence saying what
changed and when, and the work that was made from the older version -- the
alignment, the motions, the speaker labels -- is *marked* for a rerun rather than
changed underneath the reader (spec 8.7).

**A failed fetch is not a check.** Nothing was observed, so no check row is
written and no capture is settled by it: the job defers with the reason, which is
where a user reads it, and the 48 hour backstop still ends the sequence.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from .. import artifacts, pacing, proc, repo
from ..captions import CaptionParseError
from ..jobs import QUEUED, JobContext
from ..jobs.queue import stamp
from ..records.requests import request_alignment
from ..runtime.javascript import JavaScriptRuntime
from ..runtime.javascript import resolve as resolve_javascript
from . import command, sidecar, work
from .command import CaptureFailed
from .job import Attempt, parse_caption, video_id_from
from .requests import (
    RECHECK_JOB_KIND,
    RECHECK_PACE_WHAT,
    first_recheck_reason,
    request_recheck,
    video_of_job,
)
from .settings import CaptureSettings
from .settle import Check, Observation, decide, meeting_end, moment, state_of
from .store import insert_transcript_with_segments

logger = logging.getLogger(__name__)

#: The transcript origins this job can recheck. A local transcription of spec
#: 8.5 was made from the audio of this machine and has no source caption file to
#: compare against, so it is left alone.
CAPTION_ORIGINS: tuple[str, ...] = (sidecar.PUBLISHER_CAPTIONS, sidecar.AUTO_CAPTIONS)

#: The sentence a recheck pauses with when the video's transcript did not come
#: from captions at all. A pause rather than a failure: the work is not wrong,
#: there is nothing here for it to do, and a person reading the job list is told
#: which of the two it is.
NO_CAPTION_TRANSCRIPT = (
    "Video {video_id} was transcribed on this machine rather than captured from a "
    "caption track, so there is no source caption file to recheck (spec 8.7)."
)

#: The sentence a recheck pauses with when the video has no transcript yet. The
#: capture that stores one asks for the recheck itself, so this is the case of a
#: job asked for by hand before the captions were stored.
NO_TRANSCRIPT = (
    "Video {video_id} has no transcript yet, so there are no captions to recheck (spec 8.7)."
)

#: What a change in each signal means, in the words a person reads. The signal
#: names come from :mod:`townrecord.capture.settle`, which keeps them in the
#: spec's own order.
CHANGE_WORDS: dict[str, str] = {
    "hash": "the caption file is not the bytes it was",
    "revision_at": "the source says the caption track was revised",
    "duration_s": "the video is a different length",
}

#: The reason a rerun mark carries. It names the version the work was made from
#: rather than saying only that something is stale, because the mark is read
#: months later beside a transcript that has moved on since.
STALE_REASON = (
    "A revision of the captions of video {video_id} was stored at {when}, after this work "
    "was made from the older version."
)

#: How a recheck of a video with no meeting is described. A video the portal
#: listed and no meeting was matched to still has captions worth watching.
NO_MEETING = "a video with no meeting"


@dataclass
class CaptionRecheck:
    """The handler for the `recheck_captions` job kind (spec 8.7, 16.1)."""

    #: The absolute storage root the user chose (spec 8.6).
    storage_root: Path
    settings: CaptureSettings = field(default_factory=CaptureSettings)
    #: How the command is run. Tests pass a fake that writes the files yt-dlp
    #: would have written, so no test ever reaches YouTube.
    runner: command.Runner | None = None
    #: The interpreter that runs `-m yt_dlp` (spec 8.9).
    interpreter: str | None = None
    #: The JavaScript runtime yt-dlp is told to use (spec 8.3, 8.9). None means
    #: it is resolved from the interpreter, exactly as a capture resolves it.
    javascript: JavaScriptRuntime | None = None

    def __post_init__(self) -> None:
        self.storage_root = Path(self.storage_root)
        if self.runner is None:
            self.runner = proc.run
        if self.interpreter is None:
            self.interpreter = sys.executable
        if self.javascript is None:
            self.javascript = resolve_javascript(self.interpreter)

    def __call__(self, ctx: JobContext) -> None:
        video = self._video(ctx)
        transcript = repo.latest_transcript(ctx.conn, video.id)
        if transcript is None:
            ctx.pause(NO_TRANSCRIPT.format(video_id=video.id))
        if transcript.origin not in CAPTION_ORIGINS:
            ctx.pause(NO_CAPTION_TRANSCRIPT.format(video_id=video.id))

        now = ctx.clock()
        checks = self._checks(ctx, video)
        decision = decide(
            now=now,
            captured_at=self._captured_at(ctx, video, transcript),
            meeting_ended_at=self._meeting_end(ctx, video),
            checks=checks,
        )
        if decision.settle:
            self._settle(ctx, video, decision.under_churn, now, decision.reason)
            return

        observation = self._fetch(ctx, video)
        what = observation.differs_from(self._baseline(ctx, video, checks, transcript))
        repo.insert_check(
            ctx.conn,
            video_id=video.id,
            transcript_id=transcript.id,
            checked_at=stamp(now),
            changed=what is not None,
            what_changed=what,
            observed_sha256=observation.sha256,
            observed_revision_at=observation.revision_at,
            observed_duration_s=observation.duration_s,
            note="" if what is None else f"The captions changed: {CHANGE_WORDS.get(what, what)}.",
        )
        if what is not None:
            self._revise(ctx, video, now, what)

        checks.append(Check(checked_at=now, changed=what is not None))
        after = decide(
            now=now,
            captured_at=self._captured_at(ctx, video, transcript),
            meeting_ended_at=self._meeting_end(ctx, video),
            checks=checks,
        )
        if after.settle:
            self._settle(ctx, video, after.under_churn, now, after.reason)
            return
        ctx.defer(after.reason, run_after=after.next_check_at)

    # -- what the video has --------------------------------------------------

    def _video(self, ctx: JobContext) -> repo.Video:
        """Return the video the job was enqueued for, or fail plainly."""
        video = repo.get_video(ctx.conn, video_id_from(ctx.payload))
        if video is None:
            raise CaptureFailed(f"Video {video_id_from(ctx.payload)} is not stored.")
        return video

    def _checks(self, ctx: JobContext, video: repo.Video) -> list[Check]:
        """Return the checks of this video so far, oldest first (spec 8.7)."""
        return [
            Check(checked_at=moment(row.checked_at), changed=row.changed)
            for row in repo.checks_of(ctx.conn, video.id)
        ]

    def _captured_at(self, ctx: JobContext, video: repo.Video, transcript: repo.Transcript):
        """Return when this video's first transcript was stored.

        The 24 hour window and the 48 hour backstop are both counted from the
        first capture, never from the newest version: a captioner that kept
        editing would otherwise push the settle out for ever.
        """
        first = repo.first_capture_at(ctx.conn, video.id)
        return moment(first or transcript.created_at)

    def _meeting_end(self, ctx: JobContext, video: repo.Video):
        """Return when the meeting of this video ended, or None.

        The end is the meeting's start plus the video's length, because the
        portal's listing carries no end (spec 6.2). An unknown length leaves it
        unknown, and then the "final at once" shortcut does not apply.
        """
        if video.meeting_id is None:
            return None
        meeting = repo.get_meeting(ctx.conn, video.meeting_id)
        if meeting is None:
            return None
        return meeting_end(meeting.starts_at, video.duration_s)

    def _baseline(
        self,
        ctx: JobContext,
        video: repo.Video,
        checks: list[Check],
        transcript: repo.Transcript,
    ) -> Observation:
        """Return what the source said the last time it was read (spec 8.7).

        The last check is preferred, because it is the answer the source itself
        gave last. A video that has never been checked is measured against the
        captions in hand: the artifact the transcript points at, whose hash is
        the same hash the store wrote into its name, plus the two facts the
        capture wrote into its metadata.
        """
        if checks:
            row = repo.latest_check(ctx.conn, video.id)
            if row is not None and row.observed_sha256 is not None:
                return Observation(
                    sha256=row.observed_sha256,
                    revision_at=row.observed_revision_at,
                    duration_s=row.observed_duration_s,
                )
        artifact = artifacts.get(ctx.conn, transcript.artifact_id, self.storage_root)
        if artifact is None:
            raise CaptureFailed(
                f"The artifact of transcript {transcript.id} is not stored, so there is "
                f"nothing to compare the captions against."
            )
        meta = artifact.meta
        duration = meta.get("duration_s")
        return Observation(
            sha256=artifact.sha256,
            revision_at=_text(meta.get("revision_at")),
            duration_s=float(duration) if isinstance(duration, (int, float)) else None,
        )

    # -- reading the source again --------------------------------------------

    def _fetch(self, ctx: JobContext, video: repo.Video) -> Observation:
        """Read the source again and return what it says now.

        Never returns without an observation: a fetch that failed defers the job
        with its reason, and a fetch that succeeded but wrote no caption file or
        no sidecar defers too, because neither is something a check can be
        written from (spec 16.3).
        """
        folder = work.fresh_recheck_dir(self.storage_root, video.platform_video_id)
        # The archive is inside the folder that was just emptied, so it is always
        # empty of this video and the fetch is never skipped (spec 8.7).
        archive = folder / work.RECHECK_ARCHIVE_NAME
        argv = command.capture_argv(
            interpreter=self.interpreter,
            work_dir=folder,
            archive=archive,
            url=command.watch_url(video.url, video.platform_video_id),
            js_runtime=self.javascript.argument,
        )
        # Spec 8.10: a YouTube request waits for the process pace first, and the
        # pace hands the job back rather than sleeping when the hold is long.
        pacing.pacer().wait(what=RECHECK_PACE_WHAT)
        ctx.heartbeat()
        attempt = Attempt(
            result=command.run_capture(self.runner, argv, timeout_s=self.settings.process_timeout_s)
        )
        if not attempt.result.ok:
            self._handle_failure(ctx, video, attempt)
        info_path = work.info_file(folder)
        caption_path = work.caption_file(folder)
        if info_path is None or caption_path is None:
            ctx.defer(
                f"Rechecking video {video.id} wrote no caption file or no info.json sidecar, "
                f"so nothing can be compared and the check is deferred."
            )
        info = work.read_info(info_path)
        return Observation(
            sha256=hashlib.sha256(caption_path.read_bytes()).hexdigest(),
            revision_at=sidecar.revision_at(info),
            duration_s=sidecar.duration_s(info),
        )

    def _handle_failure(self, ctx: JobContext, video: repo.Video, attempt: Attempt) -> NoReturn:
        """Decide what a non-zero exit means. Never returns normally.

        Every case here is a wait rather than a failure, for the reason the
        capture gives: a 429 is the address being asked too fast, a bot check is
        not solved by signing in (spec 8.10), and a video that is temporarily
        unreachable may be reachable at the next check. The caption that is
        already stored is not lost by any of them, and the 48 hour backstop ends
        the sequence whatever happens here.
        """
        result = attempt.result
        marker = command.rate_limit_marker(result.stderr)
        if marker is not None:
            logger.warning("The recheck of video %s was rate limited (%s).", video.id, marker)
            pacing.pacer().rate_limited(
                what=RECHECK_PACE_WHAT, marker=marker, delay_s=self.settings.rate_limit_retry_s
            )
            ctx.defer(
                f"rechecking video {video.id} was rate limited by YouTube ({marker}), "
                f"will retry later",
                delay_s=self.settings.rate_limit_retry_s,
            )
        if command.bot_check_marker(result.stderr) is not None:
            logger.warning("The recheck of video %s was refused by every player client.", video.id)
            ctx.defer(
                f"YouTube asked this machine to prove it is not a robot when rechecking video "
                f"{video.id}, and TownRecord neither signs in nor uses browser cookies, so the "
                f"check waits.",
                delay_s=self.settings.rate_limit_retry_s,
            )
        last = result.last_stderr_line()
        ctx.defer(
            f"rechecking video {video.id} failed: yt-dlp exited with {result.returncode}"
            + (f": {last}" if last else " and said nothing.")
        )

    # -- what a change does --------------------------------------------------

    def _revise(self, ctx: JobContext, video: repo.Video, now: datetime, what: str) -> int | None:
        """Store a revision of the captions and raise one review item.

        Returns the id of the new transcript, or None when the same bytes were
        already stored as a version of this video (which is the case of a source
        that went back to what it said before: the older row is found, and no
        second row and no second review item are written).

        Nothing that was already exported is rewritten (spec 8.7). The new
        version is a new artifact and a new transcript row, the old one keeps its
        bytes and its hash, and the work that used it is marked for a rerun.
        """
        folder = work.recheck_dir(self.storage_root, video.platform_video_id)
        caption_path = work.caption_file(folder)
        info_path = work.info_file(folder)
        if caption_path is None or info_path is None:
            raise CaptureFailed(
                f"The recheck of video {video.id} found a change and the files it found it in "
                f"are gone, so the revision cannot be stored."
            )
        info = work.read_info(info_path)
        origin = sidecar.caption_origin(info)
        if origin is None:
            raise CaptureFailed(
                f"The info.json of the recheck of video {video.id} names no English caption "
                f"track, so the revision cannot be stored."
            )
        try:
            segments = parse_caption(caption_path)
        except CaptionParseError as exc:
            raise CaptureFailed(
                f"The caption file {caption_path.name} of the revision of video {video.id} "
                f"could not be read: {exc}"
            ) from exc
        if not segments:
            raise CaptureFailed(
                f"The caption file {caption_path.name} of the revision of video {video.id} "
                f"parsed to no caption lines, so nothing was stored."
            )
        caption_artifact = artifacts.store(
            ctx.conn,
            self.storage_root,
            artifacts.TRANSCRIPT,
            caption_path.read_bytes(),
            caption_path.suffix.lstrip("."),
            meta=_caption_meta(
                video=video,
                info=info,
                origin=origin,
                player_client="",
                js_runtime=self.javascript.argument,
            ),
        )
        known = repo.transcript_for_artifact(ctx.conn, video.id, caption_artifact.id)
        if known is not None:
            logger.info(
                "Video %s: the source went back to a version already stored (%s).",
                video.id,
                known.id,
            )
            return None
        artifacts.store(
            ctx.conn,
            self.storage_root,
            artifacts.INFO,
            info_path.read_bytes(),
            "json",
            meta={"video_id": video.platform_video_id},
        )
        transcript_id = insert_transcript_with_segments(
            ctx.conn,
            video_id=video.id,
            artifact_id=caption_artifact.id,
            origin=origin,
            segments=segments,
        )
        when = f"{now.strftime('%Y-%m-%d %H:%M')} UTC"
        sentence = (
            f"The captions of video {video.platform_video_id} changed at {when}: "
            f"{CHANGE_WORDS.get(what, what)}. The new version is stored as transcript "
            f"{transcript_id} and the older version is kept, so the alignment, the motions "
            f"and the speaker labels that were read from the older one have to be made again."
        )
        repo.raise_review_item(
            ctx.conn,
            kind=repo.REVISION_ITEM,
            subject=f"{video.platform_video_id}:{caption_artifact.id}",
            sentence=sentence,
            meeting_id=video.meeting_id,
            video_id=video.id,
        )
        logger.warning(
            "Video %s: the captions changed (%s), stored as transcript %s.",
            video.id,
            what,
            transcript_id,
        )
        logger.info("Video %s revision: transcript %s (%s).", video.id, transcript_id, when)
        self._mark_stale(ctx, video, when)
        return transcript_id

    def _mark_stale(self, ctx: JobContext, video: repo.Video, when: str) -> None:
        """Mark the work that used the older version, and ask for the alignment.

        A mark is not a change: spec 8.7 says a revision does not rewrite what
        was already exported, so the alignment, the motions and the speaker
        labels keep their rows and gain a mark saying which version replaced the
        one they were made from. The alignment is the one piece of that work the
        system can run again by itself, so it is asked for as well.
        """
        meeting_id = video.meeting_id
        if meeting_id is None:
            return
        reason = STALE_REASON.format(video_id=video.platform_video_id, when=when)
        alignment = repo.get_meeting_alignment(ctx.conn, meeting_id)
        if alignment is not None:
            repo.mark_for_rerun(
                ctx.conn,
                kind=repo.RERUN_ALIGNMENT,
                row_id=alignment.id,
                meeting_id=meeting_id,
                reason=reason,
            )
        for motion in repo.motions_of_meeting(ctx.conn, meeting_id):
            repo.mark_for_rerun(
                ctx.conn,
                kind=repo.RERUN_MOTION,
                row_id=motion.id,
                meeting_id=meeting_id,
                reason=reason,
            )
        for label in repo.speaker_labels_of_meeting(ctx.conn, meeting_id):
            repo.mark_for_rerun(
                ctx.conn,
                kind=repo.RERUN_SPEAKERS,
                row_id=label.id,
                meeting_id=meeting_id,
                reason=reason,
            )
        request_alignment(ctx.conn, meeting_id, reason=reason)

    def _settle(
        self, ctx: JobContext, video: repo.Video, under_churn: bool, now: datetime, reason: str
    ) -> None:
        """Mark every version of this video's transcript settled (spec 8.7).

        Every version and not only the newest: the older ones stopped changing
        when a revision replaced them, and a row left provisional would keep
        being preferred to the version in hand by ``latest_transcript``.
        """
        settled_at = stamp(now)
        count = repo.settle_transcripts(
            ctx.conn, video_id=video.id, settled_at=settled_at, under_churn=under_churn
        )
        logger.info(
            "Video %s settled as %s (%s rows) at %s: %s",
            video.id,
            state_of(is_provisional=False, settled_under_churn=under_churn),
            count,
            settled_at,
            reason,
        )


def next_recheck_at(conn: sqlite3.Connection, video_id: int) -> str | None:
    """Return the moment this video's next recheck is due, or None.

    The queue knows this and not the database of captures: a recheck that is not
    due yet is a queued job with ``run_after`` set (spec 16.1), which is exactly
    "the next recheck time" a user asks for. Two other cases have no moment to
    report and are answered None rather than guessed at:

    * a recheck running now has no ``run_after`` -- claiming a job clears it --
      and the moment after it is decided by the run itself;
    * a recheck that paused, and a video whose recheck was never queued, have
      nothing waiting at all.

    A queued recheck with no ``run_after`` is due as soon as a worker is free, so
    its ``created_at`` is reported: that is the earliest moment it can run, which
    is the honest reading of "due now" against a clock.
    """
    rows = conn.execute(
        "SELECT * FROM jobs WHERE kind = ? AND state = ? ORDER BY id",
        (RECHECK_JOB_KIND, QUEUED),
    ).fetchall()
    waiting = [row for row in rows if video_of_job(row["payload"]) == video_id]
    if not waiting:
        return None
    row = waiting[0]
    return row["run_after"] or row["created_at"]


def _text(value: Any) -> str | None:
    """Return a metadata value as text, or None when it is not one."""
    return value if isinstance(value, str) and value else None


def _caption_meta(
    *,
    video: repo.Video,
    info: dict[str, Any],
    origin: str,
    player_client: str,
    js_runtime: str,
) -> dict[str, Any]:
    """Return the metadata a revision's caption artifact carries (spec 8.7).

    It is the same record a first capture writes, plus the two signals the
    change test reads: the revision time the source stated and the length it
    stated. Without them the next check would have to go back to the sidecar of
    a fetch that no longer exists to find out what the previous one saw.
    """
    return {
        "video_id": video.platform_video_id,
        "origin": origin,
        "revision": True,
        "revision_at": sidecar.revision_at(info),
        "duration_s": sidecar.duration_s(info),
        "js_runtime": js_runtime,
        "player_client": player_client or command.DEFAULT_CLIENT,
    }


def register_recheck(
    *,
    storage_root: str | Path,
    registry: Any = None,
    settings: CaptureSettings | None = None,
    runner: command.Runner | None = None,
    interpreter: str | None = None,
) -> CaptionRecheck:
    """Register the caption recheck job and return the handler.

    The storage root is required and is never defaulted, for the same reason the
    capture job requires it: it is the path the user chose (spec 8.6).
    """
    from ..jobs.registry import Registry, default_registry  # noqa: PLC0415

    handler = CaptionRecheck(
        storage_root=Path(storage_root),
        settings=settings or CaptureSettings(),
        runner=runner,
        interpreter=interpreter,
    )
    chosen: Registry = default_registry if registry is None else registry
    chosen.register(RECHECK_JOB_KIND, handler, lane=command.LANE)
    return handler


__all__ = [
    "CAPTION_ORIGINS",
    "CHANGE_WORDS",
    "CaptionRecheck",
    "NO_CAPTION_TRANSCRIPT",
    "NO_TRANSCRIPT",
    "RECHECK_JOB_KIND",
    "STALE_REASON",
    "first_recheck_reason",
    "next_recheck_at",
    "register_recheck",
    "request_recheck",
]
