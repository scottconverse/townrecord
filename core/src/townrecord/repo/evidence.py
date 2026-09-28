"""Votes and citations: what a claim rests on (spec 10.4, 10.5).

A vote keeps its evidence and the kind of source it came from. A citation
points at an artifact, and the SHA-256 of that citation is read from the
artifact row rather than copied into the citation: an artifact never changes,
so its row is the hash (spec 10.5, and the versioning rule of spec 13.6).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from .rows import Citation, Vote, VoteContext
from .store import get, insert

#: The source precedence of spec 10.4, strongest first.
VOTE_SOURCE_KINDS: tuple[str, ...] = ("structured", "minutes", "packet", "transcript")


def insert_vote(
    conn: sqlite3.Connection,
    *,
    agenda_item_id: int,
    result: str,
    source_kind: str,
    evidence: str,
    tally: Mapping[str, Any] | None = None,
    motion_id: int | None = None,
    citation_id: int | None = None,
) -> int:
    """Store one vote on an item and return its id (spec 10.4).

    A transcript-only mention is never a tally, so a tally is refused with
    ``source_kind='transcript'`` by the schema. When sources disagree, one row
    per source kind is stored and none of them is picked.

    ``motion_id`` names the motion of a minutes document the outcome came out
    of, and ``citation_id`` the citation the vote rests on. Both are None for a
    vote whose evidence is its own source row.
    """
    return insert(
        conn,
        "votes",
        {
            "agenda_item_id": agenda_item_id,
            "result": result,
            "source_kind": source_kind,
            "evidence": evidence,
            "tally": None if tally is None else json.dumps(dict(tally), sort_keys=True),
            "motion_id": motion_id,
            "citation_id": citation_id,
        },
    )


def get_vote(conn: sqlite3.Connection, vote_id: int) -> Vote | None:
    """Return the vote, or None when there is no such row."""
    row = get(conn, "votes", vote_id)
    return None if row is None else Vote.from_row(row)


def votes_of_item(conn: sqlite3.Connection, agenda_item_id: int) -> list[Vote]:
    """Return the votes on an item, strongest source first (spec 10.4).

    When sources disagree, every row is returned and none of them is picked:
    the order shows which source outweighs which, and the caller can see the
    disagreement instead of a silent winner.
    """
    rows = conn.execute(
        "SELECT * FROM votes WHERE agenda_item_id = ?", (agenda_item_id,)
    ).fetchall()
    votes = [Vote.from_row(row) for row in rows]
    precedence = {kind: index for index, kind in enumerate(VOTE_SOURCE_KINDS)}
    votes.sort(key=lambda vote: (precedence.get(vote.source_kind, len(precedence)), vote.id))
    return votes


def find_votes(
    conn: sqlite3.Connection,
    *,
    body_id: int | None = None,
    identifier: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[VoteContext]:
    """Return the votes an area holds, newest meeting first (spec 13.2).

    Every filter is optional and they narrow together. ``identifier`` matches an
    agenda item's own identifiers, by key or by value, which is how a reader
    asks for everything about ordinance 2026-54 without knowing its item
    numbers. The dates are local calendar days and are read off the start of the
    meeting, not off the moment the row was written.

    Sources that disagree are all returned and none of them is picked: the order
    inside one item is the precedence of spec 10.4, strongest source first.
    """
    where: list[str] = []
    values: list[Any] = []
    if body_id is not None:
        where.append("meetings.body_id = ?")
        values.append(body_id)
    if identifier is not None:
        where.append(
            "json_valid(agenda_items.identifiers) AND EXISTS ("
            "SELECT 1 FROM json_each(agenda_items.identifiers) WHERE "
            "lower(json_each.key) = lower(?) "
            "OR lower(CAST(json_each.value AS TEXT)) = lower(?))"
        )
        values.extend([identifier, identifier])
    if from_date is not None:
        where.append("substr(meetings.starts_at, 1, 10) >= ?")
        values.append(from_date)
    if to_date is not None:
        where.append("substr(meetings.starts_at, 1, 10) <= ?")
        values.append(to_date)
    clause = "" if not where else "WHERE " + " AND ".join(where)
    rank = " ".join(f"WHEN '{kind}' THEN {index}" for index, kind in enumerate(VOTE_SOURCE_KINDS))
    rows = conn.execute(
        "SELECT votes.*, agenda_items.number AS item_number, "
        "agenda_items.title AS item_title, agenda_items.identifiers AS item_identifiers, "
        "meetings.id AS meeting_id, meetings.title AS meeting_title, "
        "meetings.starts_at AS starts_at, bodies.id AS body_id, bodies.name AS body_name "
        "FROM votes "
        "JOIN agenda_items ON agenda_items.id = votes.agenda_item_id "
        "JOIN meetings ON meetings.id = agenda_items.meeting_id "
        "JOIN bodies ON bodies.id = meetings.body_id "
        f"{clause} "
        "ORDER BY meetings.starts_at DESC, agenda_items.id, "
        f"CASE votes.source_kind {rank} ELSE {len(VOTE_SOURCE_KINDS)} END, votes.id",
        tuple(values),
    ).fetchall()
    return [
        VoteContext(
            vote=Vote.from_row(row),
            agenda_item_id=int(row["agenda_item_id"]),
            item_number=str(row["item_number"]),
            item_title=str(row["item_title"]),
            identifiers=_object(row["item_identifiers"]),
            meeting_id=int(row["meeting_id"]),
            meeting_title=row["meeting_title"],
            starts_at=str(row["starts_at"]),
            body_id=int(row["body_id"]),
            body_name=str(row["body_name"]),
        )
        for row in rows
    ]


def _object(text: str | None) -> dict[str, Any]:
    """Read a JSON object out of a text column. Anything else is empty."""
    if text is None:
        return {}
    try:
        loaded = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def insert_video_citation(
    conn: sqlite3.Connection,
    *,
    video_id: int,
    transcript_id: int,
    artifact_id: int,
    excerpt: str,
    start_ms: int,
    end_ms: int,
) -> int:
    """Store a citation to a moment in a video and return its id (spec 10.5)."""
    return insert(
        conn,
        "citations",
        {
            "kind": "video",
            "video_id": video_id,
            "transcript_id": transcript_id,
            "artifact_id": artifact_id,
            "excerpt": excerpt,
            "start_ms": start_ms,
            "end_ms": end_ms,
        },
    )


def insert_record_citation(
    conn: sqlite3.Connection,
    *,
    record_id: int,
    source_id: int,
    artifact_id: int,
    excerpt: str,
    page_number: int | None = None,
) -> int:
    """Store a citation to a document and return its id (spec 10.5).

    The page is optional: a record published as HTML has no page numbers.
    """
    return insert(
        conn,
        "citations",
        {
            "kind": "record",
            "record_id": record_id,
            "source_id": source_id,
            "artifact_id": artifact_id,
            "excerpt": excerpt,
            "page_number": page_number,
        },
    )


def get_citation(conn: sqlite3.Connection, citation_id: int) -> Citation | None:
    """Return the citation, or None when there is no such row."""
    row = get(conn, "citations", citation_id)
    return None if row is None else Citation.from_row(row)


def citation_sha256(conn: sqlite3.Connection, citation_id: int) -> str | None:
    """Return the SHA-256 the citation rests on, read from the artifact row.

    None means the citation or its artifact is gone, which is what blocks an
    export (spec 10.5). The hash is never stored on the citation itself.
    """
    row = conn.execute(
        "SELECT artifacts.sha256 FROM citations "
        "JOIN artifacts ON artifacts.id = citations.artifact_id "
        "WHERE citations.id = ?",
        (citation_id,),
    ).fetchone()
    return None if row is None else str(row["sha256"])
