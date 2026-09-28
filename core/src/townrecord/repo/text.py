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


def set_segment_speaker(conn: sqlite3.Connection, segment_id: int, name: str | None) -> None:
    """Write who is speaking on one line, or NULL when nobody was identified.

    The raw caption text is never touched: spec 10.1 and 10.6 read it as
    captioned, so a name that the captioner spelled wrongly stays as written and
    the reading of who spoke goes here instead.
    """
    conn.execute("UPDATE segments SET speaker_label = ? WHERE id = ?", (name, segment_id))


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


def agenda_item_by_number(
    conn: sqlite3.Connection, meeting_id: int, number: str
) -> AgendaItem | None:
    """Return the item a meeting carries under that number, or None."""
    row = conn.execute(
        "SELECT * FROM agenda_items WHERE meeting_id = ? AND number = ?", (meeting_id, number)
    ).fetchone()
    return None if row is None else AgendaItem.from_row(row)


def upsert_agenda_item(
    conn: sqlite3.Connection,
    *,
    meeting_id: int,
    number: str,
    title: str = "",
    identifiers: Mapping[str, Any] | None = None,
) -> tuple[int, bool]:
    """Store one agenda item, or update the one a past sync stored.

    A meeting carries one item per number, so the number is the key and a
    second sync of the same agenda writes no second row.

    A title that changed clears the time range: a boundary measured against a
    different title is evidence of nothing, and `none` with an empty reason is
    the honest state until alignment runs again (spec 10.2). Alignment is
    written by :func:`update_agenda_item_alignment`, never here.

    Returns the item id and whether the row was created.
    """
    stored = json.dumps(dict(identifiers or {}), sort_keys=True)
    found = agenda_item_by_number(conn, meeting_id, number)
    if found is None:
        return (
            insert_agenda_item(
                conn,
                meeting_id=meeting_id,
                number=number,
                title=title,
                identifiers=identifiers,
            ),
            True,
        )
    if found.title != title:
        conn.execute(
            "UPDATE agenda_items SET title = ?, identifiers = ?, start_ms = NULL, "
            "end_ms = NULL, alignment_method = 'none', alignment_reason = '' WHERE id = ?",
            (title, stored, found.id),
        )
    elif json.dumps(found.identifiers, sort_keys=True) != stored:
        conn.execute("UPDATE agenda_items SET identifiers = ? WHERE id = ?", (stored, found.id))
    return found.id, False


def update_agenda_item_alignment(
    conn: sqlite3.Connection,
    agenda_item_id: int,
    *,
    start_ms: int | None,
    end_ms: int | None,
    alignment_method: str,
    alignment_reason: str = "",
) -> None:
    """Write the time range one item was aligned to (spec 10.2).

    The schema refuses a method other than 'none' with only one end, and
    refuses a range when the method is 'none'. The check is repeated here so
    that a caller gets a sentence about its own call rather than a constraint
    error from SQLite, which names no agenda item.
    """
    if alignment_method not in ALIGNMENT_METHODS:
        raise ValueError(
            f"An alignment method is one of {', '.join(ALIGNMENT_METHODS)}, "
            f"not {alignment_method!r}."
        )
    timed = start_ms is not None or end_ms is not None
    if alignment_method == "none" and timed:
        raise ValueError("An item with no alignment carries no time range.")
    if alignment_method != "none" and (start_ms is None or end_ms is None):
        raise ValueError(f"An item aligned by {alignment_method} needs both ends of its range.")
    conn.execute(
        "UPDATE agenda_items SET start_ms = ?, end_ms = ?, alignment_method = ?, "
        "alignment_reason = ? WHERE id = ?",
        (start_ms, end_ms, alignment_method, alignment_reason, agenda_item_id),
    )


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


def latest_transcript(conn: sqlite3.Connection, video_id: int) -> Transcript | None:
    """Return the transcript of a video that should be read, or None.

    A revision is new bytes, so it is a new row (spec 8.7). A settled
    transcript is preferred over a provisional one, because a settled one has
    stopped changing (spec 10.6); among transcripts of the same standing the
    newest row wins. None means the video has no transcript at all, which is
    an honest gap rather than an empty transcript.
    """
    row = conn.execute(
        "SELECT * FROM transcripts WHERE video_id = ? ORDER BY is_provisional, id DESC LIMIT 1",
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


def items_for_segments(conn: sqlite3.Connection, transcript_id: int) -> dict[int, AgendaItem]:
    """Return the agenda item each line of a transcript falls in (spec 10.2).

    One statement for the whole transcript. Asking :func:`item_for_segment`
    once per line would run one query per line, and a full meeting is
    thousands of lines.

    The range is half open here exactly as it is there, from ``start_ms``
    inclusive to ``end_ms`` exclusive, and when two ranges both hold a line the
    most specific one wins: the item that starts last, because the range that
    opens later is the one that took over at that moment. That is the answer
    :func:`item_for_segment` gives for the same line, so the two agree.
    """
    rows = conn.execute(
        "SELECT segments.id AS segment_id, agenda_items.* FROM segments "
        "JOIN transcripts ON transcripts.id = segments.transcript_id "
        "JOIN videos ON videos.id = transcripts.video_id "
        "JOIN agenda_items ON agenda_items.meeting_id = videos.meeting_id "
        "WHERE segments.transcript_id = ? "
        "AND agenda_items.start_ms IS NOT NULL "
        "AND agenda_items.end_ms IS NOT NULL "
        "AND agenda_items.start_ms <= segments.start_ms "
        "AND segments.start_ms < agenda_items.end_ms "
        "ORDER BY segments.id, agenda_items.start_ms DESC",
        (transcript_id,),
    ).fetchall()
    found: dict[int, AgendaItem] = {}
    for row in rows:
        found.setdefault(int(row["segment_id"]), AgendaItem.from_row(row))
    return found


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
