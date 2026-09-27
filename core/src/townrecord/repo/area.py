"""The area: jurisdictions, what overlaps what, bodies, people and sources.

Section 6.1 of the spec says which levels exist, and section 7.3 says how a
source is watched. Nothing here talks to the network or decides anything: a
function stores a row the caller already has, or reads one back.
"""

from __future__ import annotations

import sqlite3

from .rows import Body, Jurisdiction, Person, Seat, Source
from .store import get, insert

#: A source that fails this many times in a row becomes broken (spec 7.3).
BROKEN_AFTER_FAILURES = 3

#: A status a failure may move to broken. A rejected source is not checked.
_CHECKED_STATUSES = ("suggested", "accepted")

#: A status a success moves back to accepted. A source that answers is not
#: broken, and one that could never leave broken could never be checked again.
_RECOVERED_STATUS = "broken"


def insert_jurisdiction(
    conn: sqlite3.Connection,
    *,
    type: str,
    name: str,
    official_id: str | None = None,
    official_id_kind: str | None = None,
    parent_id: int | None = None,
) -> int:
    """Store one jurisdiction and return its id."""
    return insert(
        conn,
        "jurisdictions",
        {
            "type": type,
            "name": name,
            "official_id": official_id,
            "official_id_kind": official_id_kind,
            "parent_id": parent_id,
        },
    )


def jurisdictions(conn: sqlite3.Connection) -> list[Jurisdiction]:
    """Return every jurisdiction, grouped by level and then by name."""
    rows = conn.execute("SELECT * FROM jurisdictions ORDER BY type, name, id").fetchall()
    return [Jurisdiction.from_row(row) for row in rows]


def bodies(conn: sqlite3.Connection, jurisdiction_id: int | None = None) -> list[Body]:
    """Return every body, or the bodies of one jurisdiction, by name."""
    if jurisdiction_id is None:
        rows = conn.execute("SELECT * FROM bodies ORDER BY name, id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM bodies WHERE jurisdiction_id = ? ORDER BY name, id", (jurisdiction_id,)
        ).fetchall()
    return [Body.from_row(row) for row in rows]


def all_sources(conn: sqlite3.Connection, jurisdiction_id: int | None = None) -> list[Source]:
    """Return every source, or the sources of one jurisdiction, in id order.

    The status is a column, so a caller sees which sources are still only
    suggested and which are broken, rather than a filtered list (spec 7.3).
    """
    if jurisdiction_id is None:
        rows = conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM sources WHERE jurisdiction_id = ? ORDER BY id", (jurisdiction_id,)
        ).fetchall()
    return [Source.from_row(row) for row in rows]


def get_jurisdiction(conn: sqlite3.Connection, jurisdiction_id: int) -> Jurisdiction | None:
    """Return the jurisdiction, or None when there is no such row."""
    row = get(conn, "jurisdictions", jurisdiction_id)
    return None if row is None else Jurisdiction.from_row(row)


def add_overlap(conn: sqlite3.Connection, jurisdiction_id: int, overlaps_id: int) -> None:
    """Record that two jurisdictions overlap (spec 6.1).

    The pair is one fact, so one row is written and the trigger in the
    migration writes the other direction. Storing the same pair twice changes
    nothing. ``ON CONFLICT DO NOTHING`` is what makes it idempotent, and it
    covers the unique key alone: a jurisdiction that overlaps itself is still
    refused, where ``OR IGNORE`` would have swallowed that too.
    """
    conn.execute(
        "INSERT INTO jurisdiction_overlaps (jurisdiction_id, overlaps_id) VALUES (?, ?) "
        "ON CONFLICT DO NOTHING",
        (jurisdiction_id, overlaps_id),
    )


def overlaps_of(conn: sqlite3.Connection, jurisdiction_id: int) -> list[int]:
    """Return the ids of the jurisdictions this one overlaps, in order."""
    rows = conn.execute(
        "SELECT overlaps_id FROM jurisdiction_overlaps WHERE jurisdiction_id = ? "
        "ORDER BY overlaps_id",
        (jurisdiction_id,),
    ).fetchall()
    return [int(row["overlaps_id"]) for row in rows]


def insert_body(conn: sqlite3.Connection, *, jurisdiction_id: int, name: str) -> int:
    """Store one body and return its id."""
    return insert(conn, "bodies", {"jurisdiction_id": jurisdiction_id, "name": name})


def get_body(conn: sqlite3.Connection, body_id: int) -> Body | None:
    """Return the body, or None when there is no such row."""
    row = get(conn, "bodies", body_id)
    return None if row is None else Body.from_row(row)


def insert_person(conn: sqlite3.Connection, *, name: str) -> int:
    """Store one person and return its id."""
    return insert(conn, "people", {"name": name})


def get_person(conn: sqlite3.Connection, person_id: int) -> Person | None:
    """Return the person, or None when there is no such row."""
    row = get(conn, "people", person_id)
    return None if row is None else Person.from_row(row)


def insert_seat(
    conn: sqlite3.Connection,
    *,
    person_id: int,
    body_id: int,
    title: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> int:
    """Store one seat and return its id. The dates are local dates as published."""
    return insert(
        conn,
        "seats",
        {
            "person_id": person_id,
            "body_id": body_id,
            "title": title,
            "start_date": start_date,
            "end_date": end_date,
        },
    )


def get_seat(conn: sqlite3.Connection, seat_id: int) -> Seat | None:
    """Return the seat, or None when there is no such row."""
    row = get(conn, "seats", seat_id)
    return None if row is None else Seat.from_row(row)


def insert_source(
    conn: sqlite3.Connection,
    *,
    jurisdiction_id: int,
    type: str,
    origin: str,
    suggested_by: str,
    reason: str,
    status: str = "suggested",
    body_id: int | None = None,
) -> int:
    """Store one source suggestion and return its id (spec 6.2).

    The person who suggested it and the reason are required by the schema, so
    a suggestion can never be stored without them.
    """
    return insert(
        conn,
        "sources",
        {
            "jurisdiction_id": jurisdiction_id,
            "body_id": body_id,
            "type": type,
            "origin": origin,
            "status": status,
            "suggested_by": suggested_by,
            "reason": reason,
        },
    )


def get_source(conn: sqlite3.Connection, source_id: int) -> Source | None:
    """Return the source, or None when there is no such row."""
    row = get(conn, "sources", source_id)
    return None if row is None else Source.from_row(row)


def record_source_failure(conn: sqlite3.Connection, source_id: int, error: str) -> int:
    """Record one failed check of a source and return the new failure count.

    A source that fails three times in a row becomes broken, and the last
    error is kept for the plain message the user sees (spec 7.3). A source the
    user rejected or that is already broken keeps its status: the count and the
    error are still recorded, so nothing fails silently.
    """
    if not str(error).strip():
        raise ValueError("A failed check is recorded with its error, never without one.")
    placeholders = ", ".join("?" for _ in _CHECKED_STATUSES)
    conn.execute(
        "UPDATE sources SET "
        "consecutive_failures = consecutive_failures + 1, "
        "last_error = ?, "
        "last_checked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
        f"status = CASE WHEN status IN ({placeholders}) AND consecutive_failures + 1 >= ? "
        "THEN 'broken' ELSE status END "
        "WHERE id = ?",
        (str(error).strip(), *_CHECKED_STATUSES, BROKEN_AFTER_FAILURES, source_id),
    )
    row = conn.execute(
        "SELECT consecutive_failures FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"There is no source {source_id}.")
    return int(row["consecutive_failures"])


def record_source_success(conn: sqlite3.Connection, source_id: int) -> None:
    """Record one successful check of a source (spec 7.3).

    The count goes back to zero and the last error is cleared: the source
    answered, so the error that was last is no longer the last thing that
    happened. A source that had gone broken is accepted again, because a
    source that answers is not broken, and one that could never leave broken
    could never be checked again. A suggested or rejected source keeps its
    status: neither is checked, so a success on one would be a bug elsewhere.
    """
    conn.execute(
        "UPDATE sources SET "
        "consecutive_failures = 0, "
        "last_error = NULL, "
        "last_checked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), "
        "status = CASE WHEN status = ? THEN 'accepted' ELSE status END "
        "WHERE id = ?",
        (_RECOVERED_STATUS, source_id),
    )
    if conn.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone() is None:
        raise KeyError(f"There is no source {source_id}.")
