"""Who is speaking, as rows (spec 10.6).

One row per run of transcript lines that the chair's words gave a speaker to
(migration ``0014_speakers.sql``). A run with no name written on it has no row
here and its lines keep a NULL ``speaker_label``, which is what spec 10.6 shows
as "an unidentified speaker".

A reading is idempotent. :func:`clear_speaker_labels` takes the names back off
the lines as well as deleting the rows, because a name left on a line whose
label row is gone is a name nothing stands behind.
"""

from __future__ import annotations

import sqlite3

from .rows import SpeakerLabel
from .store import get, insert


def insert_speaker_label(
    conn: sqlite3.Connection,
    *,
    meeting_id: int,
    transcript_id: int,
    start_segment_id: int,
    end_segment_id: int,
    spoken_name: str,
    title_kind: str,
    score: float,
    method: str,
    evidence: str,
    person_id: int | None = None,
    candidate_person_id: int | None = None,
    confirmed_by: str | None = None,
    conflict: str | None = None,
) -> int:
    """Store one label and return its id."""
    return insert(
        conn,
        "speaker_labels",
        {
            "meeting_id": meeting_id,
            "transcript_id": transcript_id,
            "start_segment_id": start_segment_id,
            "end_segment_id": end_segment_id,
            "spoken_name": spoken_name,
            "title_kind": title_kind,
            "person_id": person_id,
            "candidate_person_id": candidate_person_id,
            "score": score,
            "method": method,
            "evidence": evidence,
            "confirmed_by": confirmed_by,
            "conflict": conflict,
        },
    )


def get_speaker_label(conn: sqlite3.Connection, label_id: int) -> SpeakerLabel | None:
    """Return the label, or None when there is no such row."""
    row = get(conn, "speaker_labels", label_id)
    return None if row is None else SpeakerLabel.from_row(row)


def speaker_labels_of_meeting(conn: sqlite3.Connection, meeting_id: int) -> list[SpeakerLabel]:
    """Return the labels of one meeting's transcript, in the order they occur."""
    rows = conn.execute(
        "SELECT * FROM speaker_labels WHERE meeting_id = ? ORDER BY start_segment_id, id",
        (meeting_id,),
    ).fetchall()
    return [SpeakerLabel.from_row(row) for row in rows]


def speaker_label_of_segment(conn: sqlite3.Connection, segment_id: int) -> SpeakerLabel | None:
    """Return the label whose run holds one line, or None.

    A line whose run was never named has no label, and so does a line that falls
    in no run at all.
    """
    row = conn.execute(
        "SELECT * FROM speaker_labels WHERE start_segment_id <= ? AND end_segment_id >= ? "
        "ORDER BY start_segment_id DESC, id LIMIT 1",
        (segment_id, segment_id),
    ).fetchone()
    return None if row is None else SpeakerLabel.from_row(row)


def clear_speaker_labels(conn: sqlite3.Connection, meeting_id: int) -> int:
    """Delete one meeting's labels and take their names off the lines.

    Returns how many labels were deleted. The lines are cleared by the
    transcript the label names, so a name written by an older reading goes with
    the row it came from.
    """
    conn.execute(
        "UPDATE segments SET speaker_label = NULL WHERE transcript_id IN "
        "(SELECT transcript_id FROM speaker_labels WHERE meeting_id = ?)",
        (meeting_id,),
    )
    deleted = conn.execute(
        "DELETE FROM speaker_labels WHERE meeting_id = ?", (meeting_id,)
    ).rowcount
    return max(0, int(deleted or 0))


def mark_confirmed(conn: sqlite3.Connection, label_id: int, *, by: str) -> None:
    """Record that a second record agrees with this label.

    ``by`` names the record, not the fact: a label confirmed by the minutes and
    a label confirmed by something else are different claims, and the column
    holds which one it was.
    """
    conn.execute(
        "UPDATE speaker_labels SET confirmed_by = ? WHERE id = ? AND person_id IS NOT NULL",
        (by, label_id),
    )


def mark_conflict(conn: sqlite3.Connection, label_id: int, sentence: str) -> None:
    """Record that a second record names somebody else, and what it says.

    One label can be asked about twice: a run the chair gave a name to can be
    the mover's run of one motion and the seconder's run of another, and the
    minutes can disagree with the captions about both. Every disagreement is
    kept, one sentence per line, because the second one written over the first
    would be a silent loss of what the records say (PROJECT-BRIEF rule F). A
    reader who wants the count of disagreements reads the lines, and a reader
    who wants the labels at odds reads the rows.

    Raises:
        ValueError: The sentence is empty, or no label with that id is stored.
            An update that matched no row would report a disagreement nobody
            can go and read.
    """
    text = " ".join(str(sentence).split())
    if not text:
        raise ValueError("A conflict needs the sentence the two records disagree on.")
    row = conn.execute("SELECT conflict FROM speaker_labels WHERE id = ?", (label_id,)).fetchone()
    if row is None:
        raise ValueError(f"There is no speaker label {label_id} to record a conflict on.")
    kept = (row[0] or "").strip()
    conn.execute(
        "UPDATE speaker_labels SET conflict = ? WHERE id = ?",
        (f"{kept}\n{text}" if kept else text, label_id),
    )


__all__ = [
    "clear_speaker_labels",
    "get_speaker_label",
    "insert_speaker_label",
    "mark_confirmed",
    "mark_conflict",
    "speaker_label_of_segment",
    "speaker_labels_of_meeting",
]
