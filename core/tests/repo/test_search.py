"""Full-text search over transcripts and records (spec 12.4).

The index is not maintained by this package. Migration ``0007_search.sql``
builds it and three triggers per table move it with the row, so the tests here
write rows the ordinary way and then ask the search what it can see. That is
the point of the design: a test that wrote to the index by hand would prove
nothing about the code path the running system takes.
"""

from __future__ import annotations

import sqlite3

import pytest

from townrecord.repo import (
    RECORD_PAGE,
    SEGMENT,
    get_video,
    insert_agenda_item,
    insert_record,
    insert_record_page,
    insert_segment,
    insert_transcript,
    insert_video,
    match_expression,
    query_terms,
    search,
)

from .conftest import Area, PutArtifact


@pytest.fixture
def segment(conn: sqlite3.Connection, transcript: int) -> int:
    """One spoken line of the transcript of the fixture meeting."""
    return insert_segment(
        conn,
        transcript_id=transcript,
        start_ms=3_978_000,
        end_ms=3_985_000,
        text="The ordinance 2026-54 comes before us tonight.",
        speaker_label="Mayor",
    )


@pytest.fixture
def record_page(conn: sqlite3.Connection, area: Area, put_artifact: PutArtifact) -> int:
    """One page of a minutes document, with text about the same ordinance."""
    artifact = put_artifact("document", b"%PDF-1.4\n% minutes\n", "pdf")
    record = insert_record(conn, meeting_id=area.meeting, kind="minutes", artifact_id=artifact)
    return insert_record_page(
        conn,
        record_id=record,
        page_number=1,
        text="Ordinance 2026-54 was adopted on first reading by a voice vote.",
    )


def excerpt_of(hits: list) -> list[str]:
    """The excerpts of the hits, for a short assertion."""
    return [hit.excerpt for hit in hits]


def index_rows(conn: sqlite3.Connection, table: str, term: str) -> list:
    """The rowids the index itself holds for a word, with no join to the row.

    A search joins the index to the table it indexes, so a row that is gone
    from the table is gone from the answer whether or not the trigger moved the
    index. This reads the index alone, which is the only place a stale row can
    be seen.
    """
    return conn.execute(
        f"SELECT rowid FROM {table} WHERE {table} MATCH ?", (f'"{term}"',)
    ).fetchall()


def test_a_segment_is_searchable_as_soon_as_it_is_inserted(
    conn: sqlite3.Connection, segment: int
) -> None:
    hits = search(conn, "ordinance 2026-54")

    assert [hit.scope for hit in hits] == [SEGMENT]
    assert hits[0].start_ms == 3_978_000
    assert hits[0].end_ms == 3_985_000
    assert hits[0].speaker_label == "Mayor"
    assert hits[0].excerpt == "The ordinance 2026-54 comes before us tonight."


def test_a_record_page_is_searchable_as_soon_as_it_is_inserted(
    conn: sqlite3.Connection, record_page: int
) -> None:
    hits = search(conn, "voice vote")

    assert [hit.scope for hit in hits] == [RECORD_PAGE]
    assert hits[0].page_number == 1
    assert "voice vote" in hits[0].excerpt


def test_editing_a_segment_moves_the_index_with_it(conn: sqlite3.Connection, segment: int) -> None:
    conn.execute("UPDATE segments SET text = ? WHERE id = ?", ("Nothing said.", segment))
    conn.commit()

    assert search(conn, "ordinance") == []
    assert excerpt_of(search(conn, "nothing")) == ["Nothing said."]


def test_deleting_a_segment_removes_it_from_the_index(
    conn: sqlite3.Connection, segment: int
) -> None:
    assert search(conn, "ordinance") != []
    assert index_rows(conn, "segment_search", "ordinance") != []

    conn.execute("DELETE FROM segments WHERE id = ?", (segment,))
    conn.commit()

    assert search(conn, "ordinance") == []
    # The index itself, not just the answer: a row left behind there is a row
    # every later query still ranks against.
    assert index_rows(conn, "segment_search", "ordinance") == []


def test_deleting_a_record_page_removes_it_from_the_index(
    conn: sqlite3.Connection, record_page: int
) -> None:
    assert search(conn, "voice vote") != []
    assert index_rows(conn, "record_page_search", "voice") != []

    conn.execute("DELETE FROM record_pages WHERE id = ?", (record_page,))
    conn.commit()

    assert search(conn, "voice vote") == []
    assert index_rows(conn, "record_page_search", "voice") == []


@pytest.mark.parametrize(
    "hostile",
    [
        'ordinance "2026-54',
        "AND OR NOT NEAR",
        '"',
        "***",
        "(",
        ")))",
        "text) OR (text",
        "col:value",
        "^start",
        "-minus",
        "a OR b AND c NEAR/3 d",
        "{braces} [brackets]",
        "100% of *",
        "  ",
        "",
        "unterminated (",
        "'single'",
        "semi;colon",
        "back\\slash",
        "¿qué?",
    ],
)
def test_hostile_query_text_never_raises(
    conn: sqlite3.Connection, segment: int, hostile: str
) -> None:
    """A query is data. The worst it can do is match nothing."""
    hits = search(conn, hostile)

    assert isinstance(hits, list)


def test_a_query_of_punctuation_alone_matches_nothing(
    conn: sqlite3.Connection, segment: int
) -> None:
    assert search(conn, '"" *** --') == []
    assert match_expression('"" *** --') is None
    assert query_terms("ordinance 2026-54") == ["ordinance", "2026", "54"]


def test_the_operators_of_the_language_are_searched_for_as_words(
    conn: sqlite3.Connection, transcript: int
) -> None:
    """'AND' finds the word AND, and does not join two terms together."""
    insert_segment(
        conn, transcript_id=transcript, start_ms=0, end_ms=1_000, text="the budget and the levy"
    )
    insert_segment(
        conn, transcript_id=transcript, start_ms=1_000, end_ms=2_000, text="the budget alone"
    )

    assert search(conn, "and") != []
    # As a phrase of two words, 'budget and' matches only the line holding both.
    assert excerpt_of(search(conn, "budget and")) == ["the budget and the levy"]


def test_a_video_hit_carries_its_citation(
    conn: sqlite3.Connection, segment: int, transcript_artifact: int
) -> None:
    hit = search(conn, "ordinance")[0]
    citation = hit.citation

    assert citation.kind == "video"
    assert citation.video_id is not None
    assert citation.platform_video_id == "v-0001"
    assert citation.start_ms == 3_978_000
    assert citation.end_ms == 3_985_000
    assert citation.excerpt == "The ordinance 2026-54 comes before us tonight."
    assert citation.transcript_id is not None
    assert citation.transcript_origin == "publisher_captions"
    assert citation.transcript_artifact_sha256 is not None


def test_a_page_hit_carries_its_citation(
    conn: sqlite3.Connection, record_page: int, document_artifact: int
) -> None:
    hit = search(conn, "voice vote")[0]
    citation = hit.citation

    assert citation.kind == "record"
    assert citation.record_id is not None
    assert citation.page_number == 1
    assert citation.document_artifact_sha256 is not None
    assert citation.source_id is None  # the fixture record names no source


def test_the_type_filter_returns_pages_only_and_only_of_that_kind(
    conn: sqlite3.Connection, segment: int, record_page: int
) -> None:
    # Both the line of speech and the page hold both words, so the difference
    # between the two answers below is the filter and nothing else.
    assert {hit.scope for hit in search(conn, "ordinance 2026-54")} == {SEGMENT, RECORD_PAGE}
    assert [hit.scope for hit in search(conn, "ordinance 2026-54", type="minutes")] == [RECORD_PAGE]
    assert search(conn, "ordinance", type="agenda") == []


def test_the_level_and_body_filters_pick_the_meeting_out(
    conn: sqlite3.Connection, area: Area, segment: int
) -> None:
    assert search(conn, "ordinance", level="city") != []
    assert search(conn, "ordinance", body=area.body) != []
    assert search(conn, "ordinance", level="county") == []
    assert search(conn, "ordinance", body=area.body + 999) == []


def test_the_date_filter_uses_the_local_published_date(
    conn: sqlite3.Connection, segment: int
) -> None:
    """The meeting starts at 19:00 local. Its date is the eighth, not the ninth."""
    assert search(conn, "ordinance", date_from="2026-09-08", date_to="2026-09-08") != []
    assert search(conn, "ordinance", date_from="2026-09-09") == []
    assert search(conn, "ordinance", date_to="2026-09-07") == []


def test_an_unmatched_video_is_still_searchable_but_has_no_level(
    conn: sqlite3.Connection, area: Area, put_artifact: PutArtifact
) -> None:
    """A video is listed before it is matched to a meeting (spec 7.2 step 7)."""
    listed = insert_video(
        conn, source_id=area.channel, platform_video_id="v-0002", title="Not matched yet"
    )
    assert get_video(conn, listed).meeting_id is None
    artifact = put_artifact("transcript", b"WEBVTT\n", "vtt")
    loose = insert_transcript(
        conn, video_id=listed, artifact_id=artifact, origin="publisher_captions"
    )
    insert_segment(
        conn, transcript_id=loose, start_ms=0, end_ms=1_000, text="Good evening, everyone."
    )

    hits = search(conn, "evening")

    assert [hit.scope for hit in hits] == [SEGMENT]
    assert hits[0].meeting_id is None
    assert hits[0].level is None
    assert hits[0].citation.video_id == listed
    # Nothing says which body spoke, so a filter on the area leaves it out.
    assert search(conn, "evening", level="city") == []


def test_the_limit_cuts_the_answer(conn: sqlite3.Connection, transcript: int) -> None:
    for index in range(5):
        insert_segment(
            conn,
            transcript_id=transcript,
            start_ms=index * 1_000,
            end_ms=index * 1_000 + 500,
            text=f"the levy question number {index}",
        )

    assert len(search(conn, "levy", limit=3)) == 3
    assert search(conn, "levy", limit=0) == []
    assert search(conn, "levy", limit=-1) == []


def test_an_unknown_term_finds_nothing(conn: sqlite3.Connection, segment: int) -> None:
    assert search(conn, "aquifer") == []


def test_the_agenda_item_of_a_line_is_not_needed_to_search_it(
    conn: sqlite3.Connection, area: Area, segment: int
) -> None:
    """An item aligned over the line does not change what the index holds."""
    insert_agenda_item(
        conn,
        meeting_id=area.meeting,
        number="7",
        title="Ordinance 2026-54",
        start_ms=3_900_000,
        end_ms=4_000_000,
        alignment_method="html_video_times",
    )

    assert excerpt_of(search(conn, "ordinance")) == [
        "The ordinance 2026-54 comes before us tonight."
    ]
