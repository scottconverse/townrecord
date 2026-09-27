"""The alignment run over one meeting (spec 10.2).

One row per meeting. It holds the offset the aligner measured over the whole
video, the anchors that offset came from, and how many items each of the three
methods accounted for. A re-run of the same meeting rewrites this row: same
inputs, same rows.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

from .rows import MeetingAlignment
from .text import ALIGNMENT_METHODS


def save_meeting_alignment(
    conn: sqlite3.Connection,
    *,
    meeting_id: int,
    agenda_item_count: int,
    offset_s: int | None = None,
    offset_accepted: bool = False,
    offset_reason: str = "",
    anchors: Iterable[Mapping[str, Any]] = (),
    anchors_agreeing: int = 0,
    counts: Mapping[str, int] | None = None,
    video_id: int | None = None,
    transcript_id: int | None = None,
    spoken_reason: str | None = None,
    html_reason: str | None = None,
) -> int:
    """Write the alignment of one meeting and return the row id.

    ``counts`` is keyed by the methods of spec 10.2. A method it does not name
    is written as zero, and a name that is not a method is refused: the three
    columns of this table are the three methods, not a place to put a fourth.
    """
    for method in counts or {}:
        if method not in ALIGNMENT_METHODS:
            raise ValueError(
                f"An alignment method is one of {', '.join(ALIGNMENT_METHODS)}, not {method!r}."
            )
    if agenda_item_count < 0:
        raise ValueError("An alignment covers a number of agenda items, which is not negative.")
    found = counts or {}
    values = (
        meeting_id,
        video_id,
        transcript_id,
        int(agenda_item_count),
        offset_s,
        int(bool(offset_accepted)),
        offset_reason or "",
        json.dumps([dict(anchor) for anchor in anchors], sort_keys=True),
        int(anchors_agreeing),
        int(found.get("spoken_transitions", 0)),
        int(found.get("html_video_times", 0)),
        int(found.get("none", 0)),
        spoken_reason,
        html_reason,
    )
    conn.execute(
        "INSERT INTO meeting_alignments ("
        "meeting_id, video_id, transcript_id, agenda_item_count, offset_s, offset_accepted, "
        "offset_reason, anchors, anchors_agreeing, spoken_transitions, html_video_times, "
        "no_alignment, spoken_reason, html_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (meeting_id) DO UPDATE SET "
        "video_id = excluded.video_id, "
        "transcript_id = excluded.transcript_id, "
        "agenda_item_count = excluded.agenda_item_count, "
        "offset_s = excluded.offset_s, "
        "offset_accepted = excluded.offset_accepted, "
        "offset_reason = excluded.offset_reason, "
        "anchors = excluded.anchors, "
        "anchors_agreeing = excluded.anchors_agreeing, "
        "spoken_transitions = excluded.spoken_transitions, "
        "html_video_times = excluded.html_video_times, "
        "no_alignment = excluded.no_alignment, "
        "spoken_reason = excluded.spoken_reason, "
        "html_reason = excluded.html_reason, "
        "updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
        values,
    )
    row = conn.execute(
        "SELECT id FROM meeting_alignments WHERE meeting_id = ?", (meeting_id,)
    ).fetchone()
    return int(row["id"])


def get_meeting_alignment(conn: sqlite3.Connection, meeting_id: int) -> MeetingAlignment | None:
    """Return the alignment of a meeting, or None when it has not been aligned."""
    row = conn.execute(
        "SELECT * FROM meeting_alignments WHERE meeting_id = ?", (meeting_id,)
    ).fetchone()
    return None if row is None else MeetingAlignment.from_row(row)
