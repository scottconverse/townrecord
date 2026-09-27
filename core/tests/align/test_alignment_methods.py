"""The alignment methods, spelled the way the schema spells them (spec 10.2).

Migration ``0005_core_model.sql`` fixes the three values an agenda item's
``alignment_method`` may hold, and the aligner is the only code that produces
them. A value the aligner emits that the schema does not allow is a boundary
that cannot be stored, so these tests put the aligner's own answer into a real
migrated database instead of comparing strings.
"""

from __future__ import annotations

import sqlite3

import pytest

from townrecord.align import (
    ALIGNMENT_METHODS,
    HTML_VIDEO_TIMES,
    NO_ALIGNMENT,
    SPOKEN_TRANSITIONS,
    AlignmentResult,
    align_agenda_with_transcript,
)
from townrecord.repo import insert_agenda_item, insert_body, insert_jurisdiction, insert_meeting
from townrecord.repo.text import ALIGNMENT_METHODS as SCHEMA_METHODS

from .conftest import agenda_item, segment

#: The fixtures run on a video of exactly an hour.
HOUR_MS = 3_600_000


@pytest.fixture
def meeting(conn: sqlite3.Connection) -> int:
    """One meeting to hang agenda items on (spec 6.2)."""
    city = insert_jurisdiction(conn, type="city", name="Longmont")
    body = insert_body(conn, jurisdiction_id=city, name="City Council")
    return insert_meeting(
        conn,
        body_id=body,
        title="City Council Regular Session",
        starts_at="2026-09-08T19:00:00-06:00",
    )


def _aligned_by_all_three_methods() -> AlignmentResult:
    """One meeting whose items are placed by each of the three methods.

    Items 1 to 3 are spoken by the chair 466 s before their published times,
    which is the offset this fixture was measured with, and that makes the
    offset acceptable. Item 4's published time, shifted by the offset, lands
    where the transcript mentions its title, so it is placed by method 1. Item
    5 has no published time and is never spoken, so it keeps no boundary.
    """
    items = [
        agenda_item("1.", "CALL TO ORDER", 466),
        agenda_item("2.", "ROLL CALL", 566),
        agenda_item("3.", "APPROVAL OF THE AGENDA", 666),
        agenda_item("4.", "AWARD OF CONTRACT FOR TRANSIT PROJECT", 3000),
        agenda_item("5.", "SNOW REMOVAL CONTRACT"),
    ]
    transcript = (
        segment(0.0, "we are going to start with agenda item 1, the call to order."),
        segment(100.0, "we move to agenda item 2, roll call."),
        segment(200.0, "agenda item 3, approval of the agenda."),
        segment(2534.0, "we will take up the transit item in a moment."),
    )
    return align_agenda_with_transcript(
        agenda_items=items,
        segments=transcript,
        video_duration_ms=HOUR_MS,
    )


def test_the_aligner_and_the_database_layer_name_the_same_three_methods() -> None:
    """One list per layer, and this is what holds them together.

    The aligner emits the names; ``repo.text`` mirrors migration 0005's CHECK
    constraint for the code that writes the row. Two lists, one fact.
    """
    assert tuple(ALIGNMENT_METHODS) == tuple(SCHEMA_METHODS)
    assert (HTML_VIDEO_TIMES, SPOKEN_TRANSITIONS, NO_ALIGNMENT) == SCHEMA_METHODS


def test_every_method_the_aligner_emits_is_one_the_schema_allows() -> None:
    """The three names come from migration 0005, not from this package's prose."""
    result = _aligned_by_all_three_methods()
    assert set(result.counts_by_method()) == set(SCHEMA_METHODS)


def test_an_aligned_item_is_stored_with_its_method(conn: sqlite3.Connection, meeting: int) -> None:
    """A real insert proves the values: the schema refuses anything else."""
    result = _aligned_by_all_three_methods()
    for item in result.items:
        insert_agenda_item(
            conn,
            meeting_id=meeting,
            number=item.number,
            title=item.title,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
            alignment_method=item.method,
            alignment_reason=item.reason or "",
        )
    stored = {
        row["number"]: row["alignment_method"]
        for row in conn.execute("SELECT number, alignment_method FROM agenda_items")
    }
    assert stored == {item.number: item.method for item in result.items}
    assert len(stored) == 5, "one row for each of the fixture's items"
