"""The core data model (spec 6.2) as frozen rows.

One dataclass per table of migration ``0005_core_model.sql``, with the column
names of the table. A row is data, not an object graph: it holds the foreign
keys the table holds, and a reader joins what it needs.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, fields
from typing import Any, Self


@dataclass(frozen=True)
class Row:
    """A row of one table. ``from_row`` reads the columns by name.

    A column that SQLite holds as 0 or 1 and that is declared ``bool`` here is
    returned as a real True or False, so a caller never tests a flag against
    an integer.
    """

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        values: dict[str, Any] = {}
        for field in fields(cls):
            value = row[field.name]
            if field.type in (bool, "bool"):
                value = bool(value)
            values[field.name] = value
        return cls(**values)


@dataclass(frozen=True)
class Jurisdiction(Row):
    """A state, county, city, school district, special district or federal."""

    id: int
    type: str
    name: str
    official_id: str | None
    official_id_kind: str | None
    parent_id: int | None
    created_at: str


@dataclass(frozen=True)
class Body(Row):
    """A group that holds public meetings."""

    id: int
    jurisdiction_id: int
    name: str
    created_at: str


@dataclass(frozen=True)
class Person(Row):
    """An elected or appointed official."""

    id: int
    name: str
    created_at: str


@dataclass(frozen=True)
class Seat(Row):
    """One person holding one title on one body."""

    id: int
    person_id: int
    body_id: int
    title: str
    start_date: str | None
    end_date: str | None
    created_at: str


@dataclass(frozen=True)
class Source(Row):
    """A place where a body or a jurisdiction publishes something."""

    id: int
    jurisdiction_id: int
    body_id: int | None
    type: str
    origin: str
    status: str
    suggested_by: str
    reason: str
    consecutive_failures: int
    last_error: str | None
    last_checked_at: str | None
    created_at: str


@dataclass(frozen=True)
class Meeting(Row):
    """One sitting of one body."""

    id: int
    body_id: int
    title: str | None
    starts_at: str
    type: str
    is_cancelled: bool
    is_continued: bool
    created_at: str


@dataclass(frozen=True)
class Video(Row):
    """A recording on a video source."""

    id: int
    source_id: int
    meeting_id: int | None
    platform_video_id: str
    title: str | None
    url: str | None
    published_at: str | None
    duration_s: int | None
    is_primary: bool
    capture_state: str
    readiness: str
    created_at: str


@dataclass(frozen=True)
class Record(Row):
    """A document of a meeting, stored as an artifact."""

    id: int
    meeting_id: int
    source_id: int | None
    kind: str
    title: str | None
    artifact_id: int
    page_count: int | None
    created_at: str


@dataclass(frozen=True)
class RecordPage(Row):
    """One page of a record, with its text and its printed footer number."""

    id: int
    record_id: int
    page_number: int
    footer_page_number: int | None
    text: str


@dataclass(frozen=True)
class Transcript(Row):
    """Timed text for a video, held as an artifact."""

    id: int
    video_id: int
    artifact_id: int
    origin: str
    is_provisional: bool
    settled_under_churn: bool
    settled_at: str | None
    created_at: str


@dataclass(frozen=True)
class Segment(Row):
    """One timed line of a transcript. It names no agenda item (spec 10.2)."""

    id: int
    transcript_id: int
    start_ms: int
    end_ms: int
    text: str
    speaker_label: str | None


@dataclass(frozen=True)
class AgendaItem(Row):
    """A numbered item of a meeting, with its time range when one is known."""

    id: int
    meeting_id: int
    number: str
    title: str
    identifiers: dict[str, Any]
    start_ms: int | None
    end_ms: int | None
    alignment_method: str
    alignment_reason: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        values = {field.name: row[field.name] for field in fields(cls)}
        values["identifiers"] = _json_object(row["identifiers"])
        return cls(**values)


@dataclass(frozen=True)
class Vote(Row):
    """The result on an item, with its evidence and its source."""

    id: int
    agenda_item_id: int
    result: str
    source_kind: str
    evidence: str
    tally: dict[str, Any] | None
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        values = {field.name: row[field.name] for field in fields(cls)}
        tally = row["tally"]
        values["tally"] = None if tally is None else _json_object(tally)
        return cls(**values)


@dataclass(frozen=True)
class Citation(Row):
    """A pointer to evidence (spec 10.5).

    ``artifact_id`` is the cited artifact. The SHA-256 of a citation is read
    from that artifact row, never copied into this one.
    """

    id: int
    kind: str
    video_id: int | None
    transcript_id: int | None
    record_id: int | None
    source_id: int | None
    artifact_id: int
    excerpt: str
    start_ms: int | None
    end_ms: int | None
    page_number: int | None
    created_at: str


def _json_object(text: Any) -> dict[str, Any]:
    """Read a JSON object from a text column. Anything else is an empty one."""
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
