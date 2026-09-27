"""Asking again for the work of a meeting that could not be done yet.

Spec 10.2 and spec 10.4 both describe work that can be waiting on a record that
is not stored yet: the alignment of a meeting waits on its transcript, and the
reading of a meeting's votes waits on the next session's packet, which is where
spec 9.4 puts the draft minutes. A waiting job pauses rather than blocks, and a
paused job is not a job that waits: the queue never picks it up again on its own.
Something has to ask for it, and two things can (spec 16.3):

* the sync of the meeting, which has just listed it again, so its agenda and its
  video are known to be there;
* the work that stored the thing the job was missing, which is the video's own
  capture lane for a transcript, and the reading of a packet's pages for the
  minutes.

Lane 2 calls :func:`request_alignment` after a transcript of a meeting's primary
video is stored, and the reading of a packet's pages calls
:func:`request_minutes_of_packet` once its pages are written. Nothing in
``townrecord.capture`` is read here, because asking is the whole of what this
module does. Every function here is safe to repeat: at most one job of one
meeting and one kind waits on the queue, so a second ask does nothing rather
than queue a second run of the same work.

One thing this module cannot do is know *which* session's minutes a packet holds.
Spec 9.4 makes it the session before it, and "before" is a fact about dates
rather than something a packet's name says, so a few earlier regular sessions of
the same body are asked about by name and each reading answers for itself.
"""

from __future__ import annotations

import json
import sqlite3

from ..jobs import PAUSED, QUEUED, RUNNING, enqueue, requeue_paused
from ..repo import (
    Record,
    get_meeting,
    meetings_of_body,
    minutes_document,
)
from .portal import ALIGN_MEETING, PACKET_KIND, READ_MINUTES, AlignRequest, MinutesRequest

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

#: The same, for the reading of a meeting's minutes. It is its own sentence
#: rather than a use of :data:`ASKED_FOR`, because a reason that says the wrong
#: work was asked for is worse than no reason at all.
MINUTES_ASKED_FOR = (
    "The reading of the minutes of meeting {meeting_id} was asked for again, so it runs now."
)

#: Why a minutes reading came back to the queue when a packet was read.
PACKET_READ = (
    "The pages of packet {record_id} of meeting {meeting_id} were read, and they may hold the "
    "minutes of an earlier session, so the reading runs now."
)

#: How many of the regular sessions before a packet's own are asked about. Spec
#: 9.4 puts a session's draft minutes in the next regular session's packet, so
#: the session whose minutes a packet holds is one of the few before it. Three
#: covers a packet stored late, and every candidate is asked about by name.
MINUTES_LOOKBACK = 3


def request_alignment(
    conn: sqlite3.Connection, meeting_id: int, *, reason: str | None = None
) -> int | None:
    """Put one meeting's alignment back on the queue, or queue it for the first time.

    Returns the id of the job that was made ready, or None when nothing was
    done: a meeting nobody stored has nothing to align, and a meeting whose
    align job is already queued or running needs no second one.

    A paused job is the case this exists for. It is put back with its own reason
    kept, and it is the same row that runs, so the history of one meeting's
    alignment stays one job rather than a column of them.
    """
    meeting = get_meeting(conn, meeting_id)
    if meeting is None:
        return None
    return _request(
        conn,
        ALIGN_MEETING,
        meeting_id,
        AlignRequest(meeting_id=meeting_id, source_id=meeting.portal_source_id).as_payload(),
        reason or ASKED_FOR.format(meeting_id=meeting_id),
    )


def request_minutes(
    conn: sqlite3.Connection, meeting_id: int, *, reason: str | None = None
) -> int | None:
    """Put one meeting's minutes reading back on the queue, or queue it first.

    The same cases as an alignment, and the same answer for each: a meeting
    nobody stored has no minutes to read, a reading already queued or running
    needs no second one, and a reading that paused is the row that runs again
    with this reason on it.
    """
    if get_meeting(conn, meeting_id) is None:
        return None
    return _request(
        conn,
        READ_MINUTES,
        meeting_id,
        MinutesRequest(meeting_id=meeting_id).as_payload(),
        reason or MINUTES_ASKED_FOR.format(meeting_id=meeting_id),
    )


def request_minutes_of_packet(conn: sqlite3.Connection, record: Record) -> list[int]:
    """Ask for the minutes that a packet whose pages were just read may hold.

    Spec 9.4: a session's draft minutes sit in the next regular session's packet,
    so a packet holds the minutes of an earlier session of the same body. Which
    session that is is not written on the packet, so the few regular sessions
    before it are asked about, and one whose minutes have already been found is
    skipped: the reading for it would only write the same rows again.

    Returns the ids of the jobs that were made ready, which is empty for a record
    that is not a packet and for a packet none of whose earlier sessions need a
    reading.
    """
    if record.kind != PACKET_KIND:
        return []
    meeting = get_meeting(conn, record.meeting_id)
    if meeting is None:
        return []

    earlier = [
        other
        for other in meetings_of_body(conn, meeting.body_id)
        if other.starts_at < meeting.starts_at
        and other.type == "regular"
        and not other.is_cancelled
    ]
    asked: list[int] = []
    for other in reversed(earlier[-MINUTES_LOOKBACK:]):
        if minutes_document(conn, other.id) is not None:
            continue
        job_id = request_minutes(
            conn,
            other.id,
            reason=PACKET_READ.format(record_id=record.id, meeting_id=meeting.id),
        )
        if job_id is not None:
            asked.append(job_id)
    return asked


def _request(
    conn: sqlite3.Connection,
    kind: str,
    meeting_id: int,
    payload: dict[str, object],
    reason: str,
) -> int | None:
    """Make one meeting's job of one kind ready, or say there is nothing to do.

    None means the work is already on the queue or being done. A paused job is
    preferred to a new one, and the oldest paused job is the one that runs, so a
    meeting's history of one kind of work stays as few jobs as it can.
    """
    known = _jobs_of(conn, kind, meeting_id)
    if any(row["state"] in (QUEUED, RUNNING) for row in known):
        return None

    paused = [row for row in known if row["state"] == PAUSED]
    if paused:
        job_id = int(paused[0]["id"])
        requeue_paused(conn, job_id, reason=reason)
        return job_id

    return enqueue(conn, kind, payload)


def _jobs_of(conn: sqlite3.Connection, kind: str, meeting_id: int) -> list[sqlite3.Row]:
    """Every job of one kind for one meeting, oldest first.

    The meeting is read out of each payload rather than sought in SQL, because
    the payload is JSON text and what a job names is a fact about the job. A
    payload that cannot be read names no meeting, and that row is passed over
    rather than raising: this is one row of a lookup, and a lookup that cannot
    read one row still answers about the others.
    """
    rows = conn.execute("SELECT * FROM jobs WHERE kind = ? ORDER BY id", (kind,)).fetchall()
    return [row for row in rows if _meeting_of(row["payload"]) == meeting_id]


def _meeting_of(payload: str | None) -> int | None:
    """The meeting id a job's payload names, or None when it names none."""
    try:
        decoded = json.loads(payload or "null")
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    named = decoded.get("meeting_id")
    return named if isinstance(named, int) else None


__all__ = [
    "ASKED_FOR",
    "MINUTES_ASKED_FOR",
    "MINUTES_LOOKBACK",
    "PACKET_READ",
    "SYNCED_AGAIN",
    "TRANSCRIPT_STORED",
    "request_alignment",
    "request_minutes",
    "request_minutes_of_packet",
]
