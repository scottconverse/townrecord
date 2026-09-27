"""Transcripts, their segments, and the agenda items they are aligned to.

``segments`` names no agenda item. Section 10.2 of the spec says why, and says
where the item comes from instead: the time ranges on ``agenda_items``, read
at the moment a caller asks. ``item_for_segment`` is that read.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from .rows import AgendaItem, Segment, Transcript
from .store import get, insert

#: The methods of spec 10.2 that may produce a boundary. 'none' is the third
#: one: no alignment, with a named reason and no invented boundary.
ALIGNMENT_METHODS: tuple[str, ...] = ("html_video_times", "spoken_transitions", "none")


def insert_transcript(
    conn: sqlite3.Connection,
    *,
    video_id: int,
    artifact_id: int,
    origin: str,
    is_provisional: bool = True,
    settled_under_churn: bool = False,
    settled_at: str | None = None,
) -> int:
    """Store one transcript of a video and return its id (spec 8.7).

    A revision is new bytes, so it is a new artifact and a new row. Both a
    transcript and a record point at an artifact, never at a path.
    """
    return insert(
        conn,
        "transcripts",
        {
            "video_id": video_id,
            "artifact_id": artifact_id,
            "origin": origin,
            "is_provisional": int(is_provisional),
            "settled_under_churn": int(settled_under_churn),
            "settled_at": settled_at,
        },
    )


def get_transcript(conn: sqlite3.Connection, transcript_id: int) -> Transcript | None:
    """Return the transcript, or None when there is no such row."""
    row = get(conn, "transcripts", transcript_id)
    return None if row is None else Transcript.from_row(row)


def insert_segment(
    conn: sqlite3.Connection,
    *,
    transcript_id: int,
    start_ms: int,
    end_ms: int,
    text: str,
    speaker_label: str | None = None,
) -> int:
    """Store one timed line and return its id.

    There is no agenda item argument here: a segment cannot name one.
    """
    return insert(
        conn,
        "segments",
        {
            "transcript_id": transcript_id,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": text,
            "speaker_label": speaker_label,
        },
    )


def get_segment(conn: sqlite3.Connection, segment_id: int) -> Segment | None:
    """Return the segment, or None when there is no such row."""
    row = get(conn, "segments", segment_id)
    return None if row is None else Segment.from_row(row)


def insert_agenda_item(
    conn: sqlite3.Connection,
    *,
    meeting_id: int,
    number: str,
    title: str = "",
    identifiers: Mapping[str, Any] | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    alignment_method: str = "none",
    alignment_reason: str = "",
) -> int:
    """Store one agenda item and return its id (spec 10.2).

    An aligned item carries a whole time range: the schema refuses a method
    with only one end, and refuses a range when the method is 'none'.
    ``alignment_reason`` is where the named reason of method 3 goes. It is
    free text and not a required argument, because the item is stored as soon
    as the agenda is read, which is before alignment has run.
    """
    return insert(
        conn,
        "agenda_items",
        {
            "meeting_id": meeting_id,
            "number": number,
            "title": title,
            "identifiers": json.dumps(dict(identifiers or {}), sort_keys=True),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "alignment_method": alignment_method,
            "alignment_reason": alignment_reason,
        },
    )


def get_agenda_item(conn: sqlite3.Connection, agenda_item_id: int) -> AgendaItem | None:
    """Return the agenda item, or None when there is no such row."""
    row = get(conn, "agenda_items", agenda_item_id)
    return None if row is None else AgendaItem.from_row(row)


def agenda_items(conn: sqlite3.Connection, meeting_id: int) -> list[AgendaItem]:
    """Return the items of a meeting, in time order, untimed ones last."""
    rows = conn.execute(
        "SELECT * FROM agenda_items WHERE meeting_id = ? "
        "ORDER BY start_ms IS NULL, start_ms, number",
        (meeting_id,),
    ).fetchall()
    return [AgendaItem.from_row(row) for row in rows]


def transcript_for_artifact(
    conn: sqlite3.Connection, video_id: int, artifact_id: int
) -> Transcript | None:
    """Return the transcript of a video made from these exact bytes, or None.

    The same captions stored twice are the same artifact (spec 8.6), and the
    unique index on ``(video_id, artifact_id)`` makes them the same transcript.
    This read is how a caller finds that out before it writes anything
    (spec 8.7).
    """
    row = conn.execute(
        "SELECT * FROM transcripts WHERE video_id = ? AND artifact_id = ?",
        (video_id, artifact_id),
    ).fetchone()
    return None if row is None else Transcript.from_row(row)


def sister_transcript(conn: sqlite3.Connection, video_id: int) -> Transcript | None:
    """Return a transcript of another video of the same meeting, or None.

    Spec 8.4.1 is the first fallback when a video has no usable captions: "The
    sister channel. The same meeting on another watched channel." A meeting is
    one row in ``meetings`` and the videos of that meeting are the rows in
    ``videos`` that point at it, whichever channel published them, so the
    sister is a transcript of an *other* video of the same meeting that we
    already hold.

    A settled transcript is preferred to a provisional one, and among equals
    the earliest row wins. The order is fixed so that the same meeting answers
    the same way twice; a transcript that is still inside the 24 hour window of
    spec 8.7 may yet be replaced, and one outside it may not.

    A video that is not matched to a meeting has no sister, and neither has the
    only captured video of its meeting: None.
    """
    row = conn.execute(
        "SELECT transcripts.* FROM transcripts "
        "JOIN videos ON videos.id = transcripts.video_id "
        "JOIN videos AS ours ON ours.id = ? "
        "WHERE videos.meeting_id = ours.meeting_id "
        "AND transcripts.video_id <> ours.id "
        "AND ours.meeting_id IS NOT NULL "
        "ORDER BY transcripts.is_provisional, transcripts.id "
        "LIMIT 1",
        (video_id,),
    ).fetchone()
    return None if row is None else Transcript.from_row(row)


def segments_of(conn: sqlite3.Connection, transcript_id: int) -> list[Segment]:
    """Return the timed lines of a transcript, in time order."""
    rows = conn.execute(
        "SELECT * FROM segments WHERE transcript_id = ? ORDER BY start_ms, id",
        (transcript_id,),
    ).fetchall()
    return [Segment.from_row(row) for row in rows]


def item_for_segment(conn: sqlite3.Connection, segment_id: int) -> AgendaItem | None:
    """Return the agenda item a segment falls in, or None (spec 10.2).

    The item is resolved here, from the time range of the item on the same
    meeting, and never stored on the segment. The range is half open, from
    ``start_ms`` inclusive to ``end_ms`` exclusive, so two items that touch do
    not both claim the same line. The test is on the start of the segment,
    which is the moment the line begins.

    Ranges may nest: a council sits as a whole for the consent agenda and also
    as a redevelopment authority inside the same meeting, so two items of one
    meeting can each cover the same second. The most specific one is the item
    that starts last, because the range that opens later is the one that took
    over at that moment. So the answer is the item with the greatest start that
    still contains the segment.

    None is the honest answer when the segment is unknown, when its video is
    not matched to a meeting, when no item has a time range (method 3 of
    spec 10.2), or when the start of the segment is outside every range.
    """
    row = conn.execute(
        "SELECT agenda_items.* FROM segments "
        "JOIN transcripts ON transcripts.id = segments.transcript_id "
        "JOIN videos ON videos.id = transcripts.video_id "
        "JOIN agenda_items ON agenda_items.meeting_id = videos.meeting_id "
        "WHERE segments.id = ? "
        "AND agenda_items.start_ms IS NOT NULL "
        "AND agenda_items.end_ms IS NOT NULL "
        "AND agenda_items.start_ms <= segments.start_ms "
        "AND segments.start_ms < agenda_items.end_ms "
        "ORDER BY agenda_items.start_ms DESC "
        "LIMIT 1",
        (segment_id,),
    ).fetchone()
    return None if row is None else AgendaItem.from_row(row)
