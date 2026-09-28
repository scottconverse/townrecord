"""The core data model (spec 6.2) as frozen rows.

One dataclass per table, with the column names of the table. A row is data,
not an object graph: it holds the foreign keys the table holds, and a reader
joins what it needs.

The tables are the ones of migration ``0005_core_model.sql``, and, for
:class:`ScheduledRun`, of migration ``0012_schedule.sql``.
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
    #: The settings this source carries of its own, as JSON text (migration
    #: 0013). ``"{}"`` means it has none, and the reader falls back to the
    #: built-in seeds rather than inventing a setting (rule D).
    settings: str = "{}"


@dataclass(frozen=True)
class Meeting(Row):
    """One sitting of one body.

    ``portal_source_id`` and ``portal_meeting_id`` are the id a meeting portal
    listed this meeting under (spec 9.2). They are what a second sync of the
    same window looks the meeting up by, so the row is not written twice.
    """

    id: int
    body_id: int
    title: str | None
    starts_at: str
    type: str
    is_cancelled: bool
    is_continued: bool
    portal_source_id: int | None
    portal_meeting_id: int | None
    portal_html_template_id: int | None
    created_at: str


@dataclass(frozen=True)
class Video(Row):
    """A recording on a video source.

    ``source_note`` is where the plain words go when a video was listed by an
    adapter and no watched channel holds it (spec 9.2). The video is still one
    row on the source that listed it, and the note says so.
    """

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
    source_note: str | None
    created_at: str


@dataclass(frozen=True)
class Record(Row):
    """A document of a meeting, stored as an artifact.

    ``portal_document_id`` and ``portal_template_id`` are the ids the portal
    published the document under. A citation names the portal and those ids;
    the signed storage link is never one of them (spec 9.2).
    """

    id: int
    meeting_id: int
    source_id: int | None
    kind: str
    title: str | None
    artifact_id: int
    page_count: int | None
    portal_document_id: int | None
    portal_template_id: int | None
    created_at: str


@dataclass(frozen=True)
class RecordPage(Row):
    """One page of a record, with its text and its printed footer number.

    ``ocr_reason`` is filled when the page has no text layer and reading it
    needs OCR (spec 9.6), which is the one thing that tells a scan apart from a
    page a reader extracted nothing from.
    """

    id: int
    record_id: int
    page_number: int
    footer_page_number: int | None
    text: str
    ocr_reason: str | None


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
    """The result on an item, with its evidence and its source.

    ``motion_id`` names the motion of a minutes document this outcome came out
    of, and is None for a vote read from a structured record, a packet or a
    transcript. ``citation_id`` is the citation the vote rests on: the packet
    page for a minutes vote, the moment in the video for a transcript one.
    """

    id: int
    agenda_item_id: int
    result: str
    source_kind: str
    evidence: str
    tally: dict[str, Any] | None
    motion_id: int | None
    citation_id: int | None
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


@dataclass(frozen=True)
class MeetingAlignment(Row):
    """One alignment run over one meeting (spec 10.2, migration 0009).

    The offset is the one measured over the whole video and ``anchors`` are the
    items it was measured from, so a reader can see why the boundaries are
    where they are without re-running the aligner. One meeting has one row: a
    second run rewrites it.
    """

    id: int
    meeting_id: int
    video_id: int | None
    transcript_id: int | None
    agenda_item_count: int
    offset_s: int | None
    offset_accepted: bool
    offset_reason: str
    anchors: list[dict[str, Any]]
    anchors_agreeing: int
    spoken_transitions: int
    html_video_times: int
    no_alignment: int
    spoken_reason: str | None
    html_reason: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        values = {field.name: row[field.name] for field in fields(cls)}
        values["offset_accepted"] = bool(row["offset_accepted"])
        values["anchors"] = _json_list(row["anchors"])
        return cls(**values)


@dataclass(frozen=True)
class VoteContext(Row):
    """One vote with the item, the meeting and the body it belongs to.

    A list of votes read across meetings has to say where each one came from,
    and a caller reading it has no id in hand to look the rest up by. The join
    is therefore done once, here, rather than once per row by every caller.
    """

    vote: Vote
    agenda_item_id: int
    item_number: str
    item_title: str
    identifiers: dict[str, Any]
    meeting_id: int
    meeting_title: str | None
    starts_at: str
    body_id: int
    body_name: str


@dataclass(frozen=True)
class MinutesDocument(Row):
    """Where the minutes of one meeting were read from (spec 9.4).

    The pages are in another meeting's record, because the draft of one
    session's minutes sits in the next regular session's packet until it is
    approved. A meeting with no row here has no minutes read yet, which is a
    different fact from minutes that were searched for and not found: the job
    that searched leaves its reason on its own row.
    """

    id: int
    meeting_id: int
    record_id: int
    start_page: int
    end_page: int
    page_count: int
    head: str
    created_at: str


@dataclass(frozen=True)
class Motion(Row):
    """One motion as a minutes document records it (spec 10.4).

    An item can carry several motions, which is why a motion is a row of its
    own rather than a vote: the September 8, 2026 minutes moved seven times on
    item 9.B, and the amendments are how the item reached the outcome the
    ``votes`` row holds.
    """

    id: int
    meeting_id: int
    record_id: int
    page_number: int
    ordinal: int
    mover: str
    seconder: str
    text: str
    result: str
    outcome: str
    approved: list[str]
    dissented: list[str]
    abstained: list[str]
    tally: dict[str, Any] | None
    evidence: str
    citation_id: int | None
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Self:
        values: dict[str, Any] = {field.name: row[field.name] for field in fields(cls)}
        for name in ("approved", "dissented", "abstained"):
            values[name] = _json_strings(row[name])
        tally = row["tally"]
        values["tally"] = None if tally is None else _json_object(tally)
        return cls(**values)


@dataclass(frozen=True)
class MotionItem(Row):
    """One agenda item a motion was a motion on, and what the link rests on."""

    id: int
    motion_id: int
    agenda_item_id: int
    link_kind: str
    evidence: str
    created_at: str


def _json_strings(text: Any) -> list[str]:
    """Read a JSON list of strings from a text column. Anything else is empty."""
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    return [entry for entry in loaded if isinstance(entry, str)]


@dataclass(frozen=True)
class ScheduledRun(Row):
    """One run the daily schedule made, or could not make (spec 16.2).

    ``local_date`` is the day the run belongs to in ``time_zone``, which is
    what "once per local day" is counted in. ``state`` is ``enqueued`` with the
    job it started, or ``paused`` with the plain reason it could not run.
    """

    id: int
    task: str
    subject: str
    local_date: str
    time_zone: str
    state: str
    job_id: int | None
    reason: str | None
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


def _json_list(text: Any) -> list[dict[str, Any]]:
    """Read a JSON list of objects from a text column. Anything else is empty."""
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except (TypeError, ValueError):
        return []
    if not isinstance(loaded, list):
        return []
    return [entry for entry in loaded if isinstance(entry, dict)]
