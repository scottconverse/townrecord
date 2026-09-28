"""The motions of the minutes, and where a meeting's minutes were found.

Migration ``0011_motions.sql`` holds three tables and this module is the small
typed access to them: the run of pages one meeting's minutes were read out of
(spec 9.4), the motions those pages record (spec 10.4), and the agenda items
each motion is a motion on.

Deleting is part of the interface because reading the minutes is idempotent.
What the reading wrote for one meeting is removed and written again, and the
order the rows have to go in is a fact about the foreign keys rather than about
the caller, so it lives here: a vote names its motion, and a motion names the
citation it rests on, so votes go before motions and motions before citations.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from typing import Any

from .rows import MinutesDocument, Motion, MotionItem
from .store import get, insert

#: The source kinds a minutes reading writes, and clears before writing again.
#: ``transcript`` is in it because the reading falls back to the video when the
#: minutes are not there yet (spec 10.4), and the two kinds are written by the
#: same job over the same items.
READING_SOURCE_KINDS: tuple[str, ...] = ("minutes", "transcript")

#: The job kind that reads a meeting's minutes, spelled here because the record
#: layer is above this one and importing it would be a cycle. It is pinned to
#: the kind the queue is given by a test, so the two cannot drift apart.
READ_MINUTES_KIND = "read_minutes"


def insert_minutes_document(
    conn: sqlite3.Connection,
    *,
    meeting_id: int,
    record_id: int,
    start_page: int,
    end_page: int,
    head: str,
) -> int:
    """Store where one meeting's minutes were found and return the row's id."""
    return insert(
        conn,
        "minutes_documents",
        {
            "meeting_id": meeting_id,
            "record_id": record_id,
            "start_page": start_page,
            "end_page": end_page,
            "page_count": end_page - start_page + 1,
            "head": head,
        },
    )


def minutes_document(conn: sqlite3.Connection, meeting_id: int) -> MinutesDocument | None:
    """Return where the minutes of this meeting were found, or None.

    None means no reading has found minutes for this meeting. It is not a
    statement that there are none: the reading's own job row carries the plain
    reason when it searched and did not find them (spec 10.4).
    """
    row = conn.execute(
        "SELECT * FROM minutes_documents WHERE meeting_id = ? ORDER BY id LIMIT 1", (meeting_id,)
    ).fetchone()
    return None if row is None else MinutesDocument.from_row(row)


def minutes_expectation(conn: sqlite3.Connection, meeting_id: int) -> str | None:
    """Return the plain sentence saying where this meeting's minutes are, or None.

    When the reading job searched and did not find them it paused with its
    reason on its own row (spec 10.4), and that sentence is the answer: it says
    where the minutes are expected rather than only that they are missing. None
    means no reading has paused over this meeting, so there is nothing to say
    beyond the fact that the minutes are not stored.

    A paused job is read, never a finished one: a job that failed says the
    reading broke, which is a different fact from minutes that are not out yet.
    """
    row = conn.execute(
        "SELECT last_error FROM jobs WHERE kind = ? AND state = ? AND json_valid(payload) "
        "AND json_extract(payload, '$.meeting_id') = ? ORDER BY id DESC LIMIT 1",
        (READ_MINUTES_KIND, "paused", meeting_id),
    ).fetchone()
    if row is None or row["last_error"] is None:
        return None
    text = str(row["last_error"]).strip()
    return text or None


def insert_motion(
    conn: sqlite3.Connection,
    *,
    meeting_id: int,
    record_id: int,
    page_number: int,
    ordinal: int,
    mover: str,
    seconder: str,
    text: str,
    result: str,
    outcome: str,
    evidence: str,
    approved: Sequence[str] = (),
    dissented: Sequence[str] = (),
    abstained: Sequence[str] = (),
    tally: dict[str, Any] | None = None,
    citation_id: int | None = None,
) -> int:
    """Store one motion of one meeting's minutes and return its id.

    The names the minutes print are stored as they printed them, so a reader
    sees "None" as the empty list it is rather than a list nobody could read
    (spec 10.4). A withdrawn motion was never counted, so its tally is None.
    """
    return insert(
        conn,
        "motions",
        {
            "meeting_id": meeting_id,
            "record_id": record_id,
            "page_number": page_number,
            "ordinal": ordinal,
            "mover": mover,
            "seconder": seconder,
            "text": text,
            "result": result,
            "outcome": outcome,
            "approved": json.dumps(list(approved)),
            "dissented": json.dumps(list(dissented)),
            "abstained": json.dumps(list(abstained)),
            "tally": None if tally is None else json.dumps(dict(tally), sort_keys=True),
            "evidence": evidence,
            "citation_id": citation_id,
        },
    )


def get_motion(conn: sqlite3.Connection, motion_id: int) -> Motion | None:
    """Return the motion, or None when there is no such row."""
    row = get(conn, "motions", motion_id)
    return None if row is None else Motion.from_row(row)


def motions_of_meeting(conn: sqlite3.Connection, meeting_id: int) -> list[Motion]:
    """Return the motions of a meeting's minutes, in the order they were moved."""
    rows = conn.execute(
        "SELECT * FROM motions WHERE meeting_id = ? ORDER BY ordinal", (meeting_id,)
    ).fetchall()
    return [Motion.from_row(row) for row in rows]


def motions_of_item(conn: sqlite3.Connection, agenda_item_id: int) -> list[Motion]:
    """Return every motion that was a motion on one agenda item, in order.

    The last one is the motion the item's outcome came from, and the ones
    before it are the amendments and the tables that did not carry.
    """
    rows = conn.execute(
        "SELECT motions.* FROM motions "
        "JOIN motion_items ON motion_items.motion_id = motions.id "
        "WHERE motion_items.agenda_item_id = ? ORDER BY motions.ordinal",
        (agenda_item_id,),
    ).fetchall()
    return [Motion.from_row(row) for row in rows]


def insert_motion_item(
    conn: sqlite3.Connection,
    *,
    motion_id: int,
    agenda_item_id: int,
    link_kind: str,
    evidence: str,
) -> int:
    """Store one item a motion is a motion on and return the row's id."""
    return insert(
        conn,
        "motion_items",
        {
            "motion_id": motion_id,
            "agenda_item_id": agenda_item_id,
            "link_kind": link_kind,
            "evidence": evidence,
        },
    )


def motion_items_of(conn: sqlite3.Connection, motion_id: int) -> list[MotionItem]:
    """Return the items one motion is a motion on, in item id order."""
    rows = conn.execute(
        "SELECT * FROM motion_items WHERE motion_id = ? ORDER BY agenda_item_id", (motion_id,)
    ).fetchall()
    return [MotionItem.from_row(row) for row in rows]


def clear_minutes_reading(
    conn: sqlite3.Connection,
    meeting_id: int,
    *,
    source_kinds: Iterable[str] = READING_SOURCE_KINDS,
) -> dict[str, int]:
    """Delete what a minutes reading wrote for one meeting, and count the rows.

    Called before the reading writes, so a second reading of the same minutes
    writes one set of rows rather than two. The votes are deleted by source
    kind, because the reading writes two kinds and a re-run that found the
    minutes must not delete a vote that came from the video: spec 10.4 keeps
    both sources when they disagree, and the transcript vote is what the
    earlier reading of the same meeting left.

    The motions and the row that says where the minutes are belong to the
    minutes kind and are deleted only when it is one of the kinds asked for.
    The order is fixed by the foreign keys: a vote names its motion and the
    citation it rests on, a motion names its citation, and a citation nothing
    names any more is deleted with the rest.
    """
    kinds = tuple(source_kinds)
    votes = _votes_of_meeting(conn, meeting_id, kinds)
    citations = {int(row["citation_id"]) for row in votes if row["citation_id"] is not None}
    if votes:
        conn.executemany("DELETE FROM votes WHERE id = ?", [(int(row["id"]),) for row in votes])

    items = motions = documents = 0
    if "minutes" in kinds:
        # The motions and the row that says where the minutes are were written
        # by the minutes reading and by nothing else, so they go only when that
        # kind goes. Clearing the video's kind alone leaves them alone: a vote
        # the minutes gave still names its motion, and the foreign key would
        # refuse to let the motion go first.
        motion_rows = conn.execute(
            "SELECT id, citation_id FROM motions WHERE meeting_id = ?", (meeting_id,)
        ).fetchall()
        citations |= {
            int(row["citation_id"]) for row in motion_rows if row["citation_id"] is not None
        }
        items = conn.execute(
            "DELETE FROM motion_items WHERE motion_id IN "
            "(SELECT id FROM motions WHERE meeting_id = ?)",
            (meeting_id,),
        ).rowcount
        motions = conn.execute("DELETE FROM motions WHERE meeting_id = ?", (meeting_id,)).rowcount
        documents = conn.execute(
            "DELETE FROM minutes_documents WHERE meeting_id = ?", (meeting_id,)
        ).rowcount

    if citations:
        conn.executemany(
            "DELETE FROM citations WHERE id = ?",
            [(citation_id,) for citation_id in sorted(citations)],
        )
    return {
        "motions": motions,
        "motion_items": items,
        "minutes_documents": documents,
        "votes": len(votes),
        "citations": len(citations),
    }


def _votes_of_meeting(
    conn: sqlite3.Connection, meeting_id: int, source_kinds: tuple[str, ...]
) -> list[sqlite3.Row]:
    """The votes of one meeting's items of those source kinds, oldest first."""
    if not source_kinds:
        return []
    marks = ", ".join("?" for _ in source_kinds)
    return conn.execute(
        "SELECT votes.id, votes.citation_id FROM votes "
        "JOIN agenda_items ON agenda_items.id = votes.agenda_item_id "
        f"WHERE agenda_items.meeting_id = ? AND votes.source_kind IN ({marks}) "
        "ORDER BY votes.id",
        (meeting_id, *source_kinds),
    ).fetchall()
