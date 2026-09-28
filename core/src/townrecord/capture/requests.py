"""Asking for a recheck of a capture that may still change (spec 8.7).

Spec 8.7 makes a capture provisional for 24 hours and rechecks it every 3 hours
inside that window. Something has to put the first recheck on the queue, and one
thing does: the capture itself, in the transaction that stored a provisional
transcript. After that the recheck job is its own reason to run again -- it
defers itself to the next check -- so this module is only ever the *first* ask
and the ask of a person who wants one now.

The job is per **video**, not per meeting: a meeting can carry two recordings
from two channels, and each one is rechecked against its own source. So the
lookup here is the video's jobs, which is the one difference from
:mod:`townrecord.records.requests`, whose jobs belong to a meeting.

Every function here is safe to repeat. At most one recheck of one video waits on
the queue, so a second ask does nothing rather than queue a second run: the
brief's "a recheck every 3 hours" would otherwise become two jobs racing, each
seeing the other's check and neither settling anything.
"""

from __future__ import annotations

import json
import sqlite3

from ..jobs import ORIGIN_MANUAL, PAUSED, QUEUED, RUNNING, enqueue, requeue_paused
from ..repo import get_video
from .settle import RECHECK_EVERY

#: The job kind of a caption recheck (spec 16.1). It is its own kind rather than
#: a flag on the capture job because the two do different things: a capture
#: fetches for the first time and may fall back to audio, a recheck fetches
#: again and may revise, and neither should be able to do the other's work by
#: accident.
RECHECK_JOB_KIND = "recheck_captions"

#: How a recheck is described in the process pace's log line (spec 8.10), so a
#: run that is waiting says which of the two YouTube asks it waits for.
RECHECK_PACE_WHAT = "a caption recheck"

#: Why a provisional capture asked for its first recheck. It promises the
#: cadence rather than a time, because the time is what the job works out.
CAPTIONS_STORED = (
    "A provisional transcript of video {video_id} was stored, so the captions are "
    "rechecked every {hours:g} hours until they settle (spec 8.7)."
)

#: The reason a caller that names none of its own leaves behind. It has to be
#: true of every caller, so it promises nothing about which one asked.
RECHECK_ASKED_FOR = (
    "The captions of video {video_id} were asked to be rechecked, so they are rechecked now."
)


def request_recheck(
    conn: sqlite3.Connection,
    video_id: int,
    *,
    reason: str | None = None,
    origin: str = ORIGIN_MANUAL,
) -> int | None:
    """Put one video's recheck back on the queue, or queue it for the first time.

    Returns the id of the job that was made ready, or None when nothing was
    done: a video nobody stored has no captions to recheck, and a video whose
    recheck is already queued or running needs no second one.

    A paused recheck is the case this exists for, exactly as it is for an
    alignment (spec 16.3): a job that paused because the meeting was not settled
    yet is put back as the same row, so one video's history of rechecks stays one
    job rather than a column of them.
    """
    if get_video(conn, video_id) is None:
        return None
    known = _jobs_of(conn, video_id)
    if any(row["state"] in (QUEUED, RUNNING) for row in known):
        return None

    text = reason or RECHECK_ASKED_FOR.format(video_id=video_id)
    paused = [row for row in known if row["state"] == PAUSED]
    if paused:
        job_id = int(paused[0]["id"])
        requeue_paused(conn, job_id, reason=text)
        return job_id

    return enqueue(conn, RECHECK_JOB_KIND, {"video_id": video_id}, origin=origin)


def _jobs_of(conn: sqlite3.Connection, video_id: int) -> list[sqlite3.Row]:
    """Every recheck job of one video, oldest first.

    The video is read out of each payload rather than sought in SQL, for the
    reason :mod:`townrecord.records.requests` gives: the payload is JSON text and
    what a job names is a fact about the job. A payload that cannot be read names
    no video, and that row is passed over rather than raising, because this is
    one row of a lookup and a lookup that cannot read one row still answers about
    the others.
    """
    rows = conn.execute(
        "SELECT * FROM jobs WHERE kind = ? ORDER BY id", (RECHECK_JOB_KIND,)
    ).fetchall()
    return [row for row in rows if video_of_job(row["payload"]) == video_id]


def video_of_job(payload: str | None) -> int | None:
    """The video id a job's payload names, or None when it names none.

    Public because the queue is not the only reader: the next recheck time of a
    video (:func:`townrecord.capture.recheck.next_recheck_at`) is a question about
    the video's queued rows, and it has to read the payload the same way this
    does rather than a second way that could disagree.
    """
    try:
        decoded = json.loads(payload or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    named = decoded.get("video_id")
    return named if isinstance(named, int) and not isinstance(named, bool) else None


def first_recheck_reason(video_id: int) -> str:
    """The reason a capture asks for the first recheck of one video with."""
    return CAPTIONS_STORED.format(video_id=video_id, hours=RECHECK_EVERY.total_seconds() / 3600.0)


__all__ = [
    "CAPTIONS_STORED",
    "RECHECK_ASKED_FOR",
    "RECHECK_JOB_KIND",
    "RECHECK_PACE_WHAT",
    "first_recheck_reason",
    "request_recheck",
    "video_of_job",
]
