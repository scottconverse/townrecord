"""Asking for the alignment of a meeting that could not be aligned yet (spec 10.2).

An align job pauses when its meeting has no transcript, and a paused job is not
a job that waits: the queue never picks it up again on its own. Something has to
ask for it, and two things can (spec 16.3):

* the sync of the meeting, which has just listed it again, so its agenda and its
  video are known to be there;
* the work that stores the transcript the job was missing, which is the video's
  own capture lane.

Lane 2 calls :func:`request_alignment` after a transcript of a meeting's primary
video is stored; nothing in ``townrecord.capture`` is read here, because asking
is the whole of what this module does. Both callers ask the same function, and
asking is safe to repeat: at most one align job of one meeting waits on the
queue, so a second ask does nothing rather than queue a second run of the same
alignment.
"""

from __future__ import annotations

import json
import sqlite3

from ..jobs import ORIGIN_MANUAL, PAUSED, QUEUED, RUNNING, enqueue, requeue_paused
from ..repo import get_meeting
from .portal import ALIGN_MEETING, AlignRequest

#: Why an align job of a meeting that was listed again came back to the queue.
SYNCED_AGAIN = "Meeting {meeting_id} was listed again by its portal, so the alignment runs again."

#: Why an align job came back to the queue when a transcript was stored for it.
TRANSCRIPT_STORED = (
    "A transcript of video {video_id} of meeting {meeting_id} was stored, so the "
    "alignment runs now."
)

#: The reason a caller that names no reason of its own leaves behind. It has to
#: be true of every caller, so it promises nothing about which one asked.
ASKED_FOR = "The alignment of meeting {meeting_id} was asked for again, so it runs now."


def request_alignment(
    conn: sqlite3.Connection,
    meeting_id: int,
    *,
    reason: str | None = None,
    origin: str = ORIGIN_MANUAL,
) -> int | None:
    """Put one meeting's alignment back on the queue, or queue it for the first time.

    Returns the id of the job that was made ready, or None when nothing was
    done: a meeting nobody stored has nothing to align, and a meeting whose
    align job is already queued or running needs no second one.

    ``origin`` is how the asker was asked for, and it is carried onto a job this
    call creates. A caller that has no origin of its own leaves the default:
    a job the user ran by hand is the manual case.

    A paused job is the case this exists for. It is put back with its own reason
    kept, and it is the same row that runs, so the history of one meeting's
    alignment stays one job rather than a column of them.
    """
    meeting = get_meeting(conn, meeting_id)
    if meeting is None:
        return None

    known = _align_jobs(conn, meeting_id)
    if any(row["state"] in (QUEUED, RUNNING) for row in known):
        return None

    paused = [row for row in known if row["state"] == PAUSED]
    if paused:
        job_id = int(paused[0]["id"])
        requeue_paused(
            conn,
            job_id,
            reason=reason or ASKED_FOR.format(meeting_id=meeting_id),
        )
        return job_id

    payload = AlignRequest(meeting_id=meeting_id, source_id=meeting.portal_source_id)
    return enqueue(conn, ALIGN_MEETING, payload.as_payload(), origin=origin)


def _align_jobs(conn: sqlite3.Connection, meeting_id: int) -> list[sqlite3.Row]:
    """Every align job of one meeting, oldest first."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE kind = ? ORDER BY id", (ALIGN_MEETING,)
    ).fetchall()
    return [row for row in rows if _meeting_of(row["payload"]) == meeting_id]


def _meeting_of(payload: str | None) -> int | None:
    """The meeting id an align payload names, or None when it names none.

    A payload written by hand, or by a version that spelled it differently, is
    read as naming no meeting rather than raising: this is one row of a lookup,
    and a lookup that cannot read one row still answers about the others.
    """
    try:
        decoded = json.loads(payload or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    named = decoded.get("meeting_id")
    return named if isinstance(named, int) else None


__all__ = ["ASKED_FOR", "SYNCED_AGAIN", "TRANSCRIPT_STORED", "request_alignment"]
