"""What happens when captions are missing or unusable (spec 8.4).

Spec 8.3 captures captions first, because they are small. Spec 8.4 says what to
do when that produced nothing usable, and the order is fixed:

1. **The sister channel (8.4.1).** "The same meeting on another watched
   channel." A meeting is one row in ``meetings``, and the videos of that
   meeting are the rows in ``videos`` that point at it, whichever channel
   published them. When another of those videos already has a transcript, this
   video is linked to it and the work is over: the meeting does have a
   transcript, and the reason it does is recorded.
2. **The "Show transcript" panel (8.4.2).** A browser would have to be driven
   and the panel scraped. TODO: spec 8.4.2 step 2, a later unit. It is named
   here so the gap is visible where the order is implemented, and it is not
   silently skipped.
3. **Audio (8.4.3, 8.5).** A ``transcribe`` job is enqueued, and it downloads
   the audio and transcribes it on this machine.

Two things are never done here. HTTP 429 is not a reason to take a fallback at
all: spec 8.3 makes it a paced retry, and the caller defers before this module
is reached. And a hand-off is never silent: the reason, the step that was taken
and the sister are recorded in the job's checkpoint, and the video's capture
state is moved off ``pending``. Spec 16.3 is the rule either way: the user reads
what happened.

The reason is kept in the checkpoint of the capture job, which is the durable
place a job already has for what it found. There is no table for a hand-off
note, and none is needed: the row that records the outcome is the ``transcripts``
row the sister step writes, and the job row itself.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .. import repo
from ..jobs import JobContext, enqueue
from ..stt.audio import AudioTrigger
from . import transcribe
from .store import insert_transcript_with_segments

logger = logging.getLogger(__name__)

#: The origin of a transcript taken from the same meeting on another channel.
#: It is the schema's own spelling: migration ``0005_core_model.sql`` line 256
#: is ``origin IN ('publisher_captions', 'auto_captions', 'sister_channel',
#: 'local_speech_to_text')``.
SISTER_CHANNEL_ORIGIN = "sister_channel"

#: The checkpoint key the capture job records the hand-off under.
CHECKPOINT_KEY = "caption_handoff"

#: The capture states a hand-off can leave behind, from ``repo.CAPTURE_STATES``.
SISTER_STATE = "captions"
AUDIO_STATE = "audio"


@dataclass(frozen=True)
class Handoff:
    """What the capture job did instead of storing captions of its own."""

    #: The capture state the video was left in: ``captions`` or ``audio``.
    state: str
    #: The plain sentence explaining the hand-off, recorded for the user.
    reason: str
    #: The reason the audio is being downloaded, or None for a sister link.
    trigger: AudioTrigger | None = None
    #: The video whose transcript was taken, or None when audio was enqueued.
    sister_video_id: int | None = None
    #: The transcript row that was written for this video, if any.
    transcript_id: int | None = None
    #: The ``transcribe`` job that was enqueued, if any.
    job_id: int | None = None

    @property
    def followed_sister(self) -> bool:
        """True when the meeting's other channel already had the transcript."""
        return self.sister_video_id is not None


def hand_off_captions(
    ctx: JobContext,
    video: repo.Video,
    *,
    reason: str,
    trigger: AudioTrigger,
) -> Handoff:
    """Take the next step of spec 8.4 for a video whose captions failed.

    The sister channel is tried first (8.4.1). When another video of the same
    meeting already has a transcript, this video is linked to it with origin
    ``sister_channel`` and nothing is downloaded. Otherwise a ``transcribe``
    job is enqueued (8.4.3).

    Args:
        ctx: The running capture job, whose connection and checkpoint are used.
        video: The video whose captions are missing or unusable.
        reason: The plain sentence saying what went wrong. It is recorded, so
            a hand-off is never a silent success.
        trigger: Why the audio may be downloaded. ``no_captions`` when no
            caption file was written, ``captions_unusable`` when one was but
            held nothing usable.

    Returns:
        The step that was taken.

    Raises:
        ValueError: The reason is blank. A hand-off is recorded with its reason,
            never without one.
    """
    text = _plain(reason)
    trigger = AudioTrigger.from_value(trigger)
    sister = repo.sister_transcript(ctx.conn, video.id)
    if sister is not None:
        return _link_sister(ctx, video, sister, reason=text, trigger=trigger)
    return enqueue_transcription(ctx, video, trigger=trigger, reason=text)


def enqueue_transcription(
    ctx: JobContext,
    video: repo.Video,
    *,
    trigger: AudioTrigger,
    reason: str,
) -> Handoff:
    """Enqueue the ``transcribe`` job for one video and record why (8.4.3).

    This is also the whole of the "Local transcription only" setting of
    spec 8.10: that setting skips the caption command rather than failing at
    it, so there is no sister to look for and the trigger says so.
    """
    text = _plain(reason)
    trigger = AudioTrigger.from_value(trigger)
    job_id = enqueue(
        ctx.conn,
        transcribe.JOB_KIND,
        transcribe.payload_for(video.id, trigger, text),
        # The lane is named rather than looked up, so the heavy lane's limit of
        # one holds even when the handler has not been registered by a caller.
        lane=transcribe.LANE,
        # The transcription is this capture's child, so it was asked for the way
        # the capture was: a scheduled capture queues a scheduled transcription
        # (spec 16.2). Without this the child is recorded as the user's ask.
        origin=ctx.origin,
    )
    handoff = Handoff(state=AUDIO_STATE, reason=text, trigger=trigger, job_id=job_id)
    _record(ctx, video, handoff)
    logger.info(
        "Video %s has no usable captions, so transcription job %s was queued (%s): %s",
        video.id,
        job_id,
        trigger.value,
        text,
    )
    return handoff


def _link_sister(
    ctx: JobContext,
    video: repo.Video,
    sister: repo.Transcript,
    *,
    reason: str,
    trigger: AudioTrigger,
) -> Handoff:
    """Link this video to the transcript of the same meeting (spec 8.4.1).

    The bytes are already stored: this writes a ``transcripts`` row for *this*
    video pointing at the artifact the sister's transcript points at, with the
    origin ``sister_channel``. The lines are copied from the sister's own rows,
    because ``segments`` belong to a transcript and cannot be shared: without
    them the new row would read like a meeting where nobody spoke, which is the
    failure this whole module exists to avoid.

    The sister's settledness is copied too. A transcript that has already left
    the 24 hour window of spec 8.7 is not made provisional again by being
    linked, and one still inside it stays inside it.
    """
    known = repo.transcript_for_artifact(ctx.conn, video.id, sister.artifact_id)
    if known is not None:
        # This video is already linked to those bytes: the same artifact is the
        # same transcript, so this run adds nothing (spec 8.6, 8.7).
        transcript_id = known.id
    else:
        transcript_id = insert_transcript_with_segments(
            ctx.conn,
            video_id=video.id,
            artifact_id=sister.artifact_id,
            origin=SISTER_CHANNEL_ORIGIN,
            segments=repo.segments_of(ctx.conn, sister.id),
            is_provisional=sister.is_provisional,
            settled_under_churn=sister.settled_under_churn,
            settled_at=sister.settled_at,
            # The link is this capture's own write, so what it queues for the
            # meeting takes after the capture (spec 16.2).
            job_origin=ctx.origin,
        )
    handoff = Handoff(
        state=SISTER_STATE,
        reason=reason,
        trigger=trigger,
        sister_video_id=sister.video_id,
        transcript_id=transcript_id,
    )
    _record(ctx, video, handoff)
    logger.info(
        "Video %s has no usable captions, so it took the transcript of video %s "
        "of the same meeting (transcript %s, origin %s): %s",
        video.id,
        sister.video_id,
        transcript_id,
        SISTER_CHANNEL_ORIGIN,
        reason,
    )
    return handoff


def _record(ctx: JobContext, video: repo.Video, handoff: Handoff) -> None:
    """Write the hand-off into the job's checkpoint and move the capture state.

    The checkpoint is one value, so what is already in it is read back first and
    kept: ``save_checkpoint`` replaces the whole column.
    """
    before = ctx.checkpoint()
    record: dict[str, Any] = dict(before) if isinstance(before, Mapping) else {}
    record[CHECKPOINT_KEY] = {
        "reason": handoff.reason,
        "state": handoff.state,
        "trigger": None if handoff.trigger is None else handoff.trigger.value,
        "sister_video_id": handoff.sister_video_id,
        "transcript_id": handoff.transcript_id,
        "job_id": handoff.job_id,
        # TODO: spec 8.4.2, the "Show transcript" panel, is not implemented. It
        # needs a browser driven against the watch page and is a later unit; it
        # sits between the sister channel above and the audio below.
    }
    ctx.save_checkpoint(record)
    repo.set_capture_state(ctx.conn, video.id, handoff.state)


def _plain(reason: str) -> str:
    """Return the reason as one plain sentence, or raise when there is none."""
    text = " ".join(str(reason).split())
    if not text:
        raise ValueError("A caption hand-off is recorded with its reason, never without one.")
    return text
