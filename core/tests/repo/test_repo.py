"""The repository layer: small typed reads and writes (spec 6.2, 7.3, 10.2)."""

from __future__ import annotations

import sqlite3

import pytest

from townrecord.repo import (
    agenda_items,
    citation_sha256,
    get_agenda_item,
    get_citation,
    get_jurisdiction,
    get_source,
    get_transcript,
    get_video,
    get_vote,
    insert_agenda_item,
    insert_record,
    insert_record_citation,
    insert_record_page,
    insert_segment,
    insert_source,
    insert_video,
    insert_video_citation,
    insert_vote,
    item_for_segment,
    record_pages,
    record_source_failure,
)

from .conftest import Area


def test_a_row_reads_back_with_its_fields(conn: sqlite3.Connection, area: Area) -> None:
    city = get_jurisdiction(conn, area.city)
    assert city is not None
    assert city.type == "city"
    assert city.name == "Longmont"
    assert city.official_id == "0845970"
    assert city.official_id_kind == "geoid"
    assert city.parent_id == area.county
    assert city.created_at.endswith("Z"), "a time the database wrote is UTC"

    assert get_jurisdiction(conn, 999_999) is None
    assert get_agenda_item(conn, 999_999) is None
    assert get_citation(conn, 999_999) is None
    assert get_vote(conn, 999_999) is None


def test_a_flag_reads_back_as_a_boolean(conn: sqlite3.Connection, area: Area) -> None:
    video = get_video(
        conn,
        insert_video(conn, source_id=area.channel, platform_video_id="v-0001", is_primary=True),
    )
    assert video is not None
    assert video.is_primary is True
    assert video.readiness == "unknown", "a listing waits for its status metadata"


def test_identifiers_round_trip_as_json(conn: sqlite3.Connection, area: Area) -> None:
    item = insert_agenda_item(
        conn,
        meeting_id=area.meeting,
        number="9.B",
        title="Second reading of an ordinance",
        identifiers={"ordinance": "O-2026-42", "packet_page": 118},
    )
    stored = get_agenda_item(conn, item)
    assert stored is not None
    assert stored.identifiers == {"ordinance": "O-2026-42", "packet_page": 118}
    assert stored.alignment_method == "none", "an item starts unaligned"


def test_a_tally_round_trips_and_a_vote_without_one_reads_none(
    conn: sqlite3.Connection, area: Area
) -> None:
    item = insert_agenda_item(conn, meeting_id=area.meeting, number="9.B")
    counted = insert_vote(
        conn,
        agenda_item_id=item,
        result="passed",
        source_kind="minutes",
        evidence="Minutes: the motion carried, 6 to 1.",
        tally={"yes": 6, "no": 1},
    )
    spoken = insert_vote(
        conn,
        agenda_item_id=item,
        result="passed",
        source_kind="transcript",
        evidence="The mayor said the motion carried.",
    )
    counted_row = get_vote(conn, counted)
    spoken_row = get_vote(conn, spoken)
    assert counted_row is not None and counted_row.tally == {"yes": 6, "no": 1}
    assert spoken_row is not None and spoken_row.tally is None


def test_the_pages_of_a_record_come_back_in_page_order(
    conn: sqlite3.Connection, area: Area, document_artifact: int
) -> None:
    record = insert_record(
        conn, meeting_id=area.meeting, kind="agenda", artifact_id=document_artifact, page_count=3
    )
    for number in (2, 1, 3):
        insert_record_page(conn, record_id=record, page_number=number, text=f"Page {number}")
    assert [page.page_number for page in record_pages(conn, record)] == [1, 2, 3]


def test_the_items_of_a_meeting_come_back_in_time_order_with_untimed_last(
    conn: sqlite3.Connection, area: Area
) -> None:
    insert_agenda_item(conn, meeting_id=area.meeting, number="3")
    insert_agenda_item(
        conn,
        meeting_id=area.meeting,
        number="2",
        start_ms=600_000,
        end_ms=1_200_000,
        alignment_method="spoken_transitions",
    )
    insert_agenda_item(
        conn,
        meeting_id=area.meeting,
        number="1",
        start_ms=0,
        end_ms=600_000,
        alignment_method="html_video_times",
    )
    assert [item.number for item in agenda_items(conn, area.meeting)] == ["1", "2", "3"]


def test_a_source_becomes_broken_after_three_failures_in_a_row(
    conn: sqlite3.Connection, area: Area
) -> None:
    """Spec 7.3: three strikes, and the user sees the last error."""
    assert record_source_failure(conn, area.portal, "timed out after 30s") == 1
    assert record_source_failure(conn, area.portal, "timed out after 30s") == 2
    source = get_source(conn, area.portal)
    assert source is not None and source.status == "accepted"

    assert record_source_failure(conn, area.portal, "connection refused") == 3
    source = get_source(conn, area.portal)
    assert source is not None
    assert source.status == "broken"
    assert source.last_error == "connection refused"
    assert source.last_checked_at is not None


def test_a_rejected_source_keeps_its_status_but_not_its_silence(
    conn: sqlite3.Connection, area: Area
) -> None:
    rejected = insert_source(
        conn,
        jurisdiction_id=area.city,
        type="website_news",
        origin="https://news.test.invalid",
        suggested_by="user",
        reason="Suggested by the user and rejected.",
        status="rejected",
    )
    for _ in range(4):
        record_source_failure(conn, rejected, "nothing there")
    source = get_source(conn, rejected)
    assert source is not None
    assert source.status == "rejected"
    assert source.consecutive_failures == 4
    assert source.last_error == "nothing there"


def test_a_failed_check_is_recorded_with_its_error(conn: sqlite3.Connection, area: Area) -> None:
    with pytest.raises(ValueError):
        record_source_failure(conn, area.portal, "   ")
    with pytest.raises(KeyError):
        record_source_failure(conn, 999_999, "timed out")


def test_the_hash_of_a_citation_comes_from_its_artifact(
    conn: sqlite3.Connection, area: Area, video: int, transcript: int, transcript_artifact: int
) -> None:
    """Spec 10.5: the hash is read from the artifact, never copied onto the row."""
    citation = insert_video_citation(
        conn,
        video_id=video,
        transcript_id=transcript,
        artifact_id=transcript_artifact,
        excerpt="Good evening.",
        start_ms=0,
        end_ms=5000,
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(citations)")}
    assert "sha256" not in columns, "an artifact never changes, so its row is the hash"

    expected = conn.execute(
        "SELECT sha256 FROM artifacts WHERE id = ?", (transcript_artifact,)
    ).fetchone()[0]
    assert citation_sha256(conn, citation) == expected
    assert len(expected) == 64
    assert citation_sha256(conn, citation + 999) is None


# -------------------------------------------------- the item of a segment --


@pytest.fixture
def aligned(conn: sqlite3.Connection, area: Area, transcript: int) -> tuple[int, int, int]:
    """Two items with ranges and one with no alignment at all (spec 10.2)."""
    first = insert_agenda_item(
        conn,
        meeting_id=area.meeting,
        number="1",
        start_ms=0,
        end_ms=600_000,
        alignment_method="html_video_times",
    )
    second = insert_agenda_item(
        conn,
        meeting_id=area.meeting,
        number="2",
        start_ms=600_000,
        end_ms=1_200_000,
        alignment_method="spoken_transitions",
    )
    untimed = insert_agenda_item(conn, meeting_id=area.meeting, number="3")
    assert get_transcript(conn, transcript) is not None
    return first, second, untimed


@pytest.mark.parametrize(
    ("start_ms", "expected"),
    [
        (0, "first"),
        (599_999, "first"),
        (600_000, "second"),
        (1_199_999, "second"),
        (1_200_000, None),
        (9_999_999, None),
    ],
    ids=[
        "the first millisecond of item 1",
        "the last millisecond of item 1",
        "the first millisecond of item 2",
        "the last millisecond of item 2",
        "the millisecond after the last item",
        "long after the meeting",
    ],
)
def test_item_for_segment_resolves_the_item_holding_the_start(
    conn: sqlite3.Connection,
    transcript: int,
    aligned: tuple[int, int, int],
    start_ms: int,
    expected: str | None,
) -> None:
    """Spec 10.2, last paragraph: the item is resolved here, not stored.

    The range is half open, so two items that touch do not both claim a line.
    """
    first, second, _untimed = aligned
    segment = insert_segment(
        conn,
        transcript_id=transcript,
        start_ms=start_ms,
        end_ms=start_ms + 1000,
        text="A line of the meeting.",
    )
    item = item_for_segment(conn, segment)
    wanted = {"first": first, "second": second, None: None}[expected]
    assert (None if item is None else item.id) == wanted
    if item is not None:
        assert item.number == ("1" if expected == "first" else "2"), "the item, not a stub"


def test_item_for_segment_says_none_for_a_line_nothing_holds(
    conn: sqlite3.Connection, transcript: int, aligned: tuple[int, int, int]
) -> None:
    segment = insert_segment(
        conn, transcript_id=transcript, start_ms=5_000_000, end_ms=5_001_000, text="Later."
    )
    assert item_for_segment(conn, segment) is None, "an untimed item claims no line"


def test_item_for_segment_says_none_for_an_unknown_segment(conn: sqlite3.Connection) -> None:
    assert item_for_segment(conn, 999_999) is None


def test_item_for_segment_says_none_when_the_video_is_not_matched_to_a_meeting(
    conn: sqlite3.Connection, area: Area, transcript: int, aligned: tuple[int, int, int]
) -> None:
    """A listing kept before it is matched has no meeting, so no items either."""
    conn.execute("UPDATE videos SET meeting_id = NULL")
    segment = insert_segment(
        conn, transcript_id=transcript, start_ms=0, end_ms=1000, text="Good evening."
    )
    assert item_for_segment(conn, segment) is None


def test_the_item_of_a_citation_is_the_item_of_its_range(
    conn: sqlite3.Connection, area: Area, document_artifact: int
) -> None:
    """A record citation rests on a page, a video citation on an item."""
    record = insert_record(
        conn, meeting_id=area.meeting, kind="minutes", artifact_id=document_artifact
    )
    insert_record_page(conn, record_id=record, page_number=1, text="Motion carried.")
    citation = insert_record_citation(
        conn,
        record_id=record,
        source_id=area.portal,
        artifact_id=document_artifact,
        excerpt="Motion carried.",
        page_number=1,
    )
    stored = get_citation(conn, citation)
    assert stored is not None
    assert stored.kind == "record"
    assert stored.page_number == 1
    assert stored.start_ms is None, "a document citation has no seconds"
