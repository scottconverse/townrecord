"""Search for a phrase that crosses a caption line (spec 12.4).

A caption line is five to eight words, so a phrase the user types often lands on
two lines. The Longmont session of 2026-09-22 speaks "motion to pass and adopt
ordinance" on one line and "2026-58." on the next, and a query for the ordinance
has to find it there. The index the search reads is ``segment_window_search``
(migration ``0008_segment_windows.sql``), which holds runs of consecutive lines
as single rows, so the tests here write lines the ordinary way and then ask what
the search can see. Nothing writes to the index by hand: a test that did would
prove nothing about the path the running system takes.

The fixture lines are the September 22 ones named above with a few neighbours,
and the real caption file of that meeting is searched too when it is on this
machine.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from townrecord.captions import parse_srv3
from townrecord.repo import (
    SEGMENT,
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_segment,
    insert_source,
    insert_transcript,
    insert_video,
    search,
)

from .conftest import PutArtifact

#: The September 22 lines and their neighbours, as the caption file has them:
#: one line names the ordinance, and the last mention of it is cut in two by a
#: line break, which is what a window index is for.
SEP22_LINES = (
    (4_230_000, 4_233_000, "we turn now to ordinance 2026-58,"),
    (4_233_000, 4_236_000, "which sets the fee schedule for permits"),
    (4_575_000, 4_578_000, "motion to pass and adopt ordinance"),
    (4_578_000, 4_581_000, "2026-58."),
    (4_590_000, 4_593_000, "all in favor say aye and the vote carries"),
    (4_600_000, 4_603_000, "unanimously."),
)


def speak(conn: sqlite3.Connection, transcript: int, lines: tuple) -> None:
    """Insert caption lines in the order the caption file has them."""
    for start_ms, end_ms, text in lines:
        insert_segment(conn, transcript_id=transcript, start_ms=start_ms, end_ms=end_ms, text=text)


@pytest.fixture
def sep22(conn: sqlite3.Connection, transcript: int) -> int:
    """The September 22 lines of the fixture meeting."""
    speak(conn, transcript, SEP22_LINES)
    return transcript


def excerpt_of(hits: list) -> list[str]:
    """The excerpts of the hits, for a short assertion."""
    return [hit.excerpt for hit in hits]


def test_a_phrase_split_across_a_line_break_is_found(conn: sqlite3.Connection, sep22: int) -> None:
    """The words 'ordinance' and '2026-58' are on two lines, and that is one hit."""
    hits = search(conn, "pass and adopt ordinance 2026-58")

    assert [hit.scope for hit in hits] == [SEGMENT]
    assert excerpt_of(hits) == ["motion to pass and adopt ordinance 2026-58."]
    # The seconds are the first line's start and the last line's end: the
    # mention is cited at both lines it was spoken between.
    assert hits[0].start_ms == 4_575_000
    assert hits[0].end_ms == 4_581_000
    assert hits[0].citation.start_ms == 4_575_000
    assert hits[0].citation.end_ms == 4_581_000
    assert hits[0].citation.excerpt == hits[0].excerpt
    assert hits[0].citation.transcript_origin == "publisher_captions"


def test_a_two_line_phrase_ranks_before_a_hit_that_only_holds_the_words(
    conn: sqlite3.Connection, transcript: int
) -> None:
    """A hit whose lines hold the query as one phrase comes first.

    'budget and levy' is the shorter window and so the better bm25 answer, and
    the phrase rule is what puts the line that says 'budget levy' in front of
    it. Both are real hits and both are returned; only the order is asserted.
    """
    speak(
        conn,
        transcript,
        (
            (0, 1_000, "the council met on a tuesday evening"),
            (1_000_000, 1_003_000, "budget and levy"),
            (3_000_000, 3_003_000, "the budget levy was approved tonight"),
            (5_000_000, 5_003_000, "nothing else was decided"),
        ),
    )

    hits = search(conn, "budget levy")

    assert [hit.start_ms for hit in hits] == [3_000_000, 1_000_000]
    assert excerpt_of(hits)[0] == "the budget levy was approved tonight"


def test_one_spoken_mention_is_one_hit(conn: sqlite3.Connection, sep22: int) -> None:
    """Every window over a mention matched, and the answer still names it once.

    'ordinance 2026-58' is spoken twice: once on a single line and once across
    two. The windows that hold each mention overlap, and a mention is reported
    once, at the lines it was spoken.
    """
    hits = search(conn, "ordinance 2026-58")

    assert [hit.start_ms for hit in hits] == [4_230_000, 4_575_000]
    assert excerpt_of(hits) == [
        "we turn now to ordinance 2026-58,",
        "motion to pass and adopt ordinance 2026-58.",
    ]


def test_removing_the_lines_leaves_no_window_behind(conn: sqlite3.Connection, sep22: int) -> None:
    """The window index itself, not just the answer.

    A window left behind after its lines are gone is a row every later query
    still ranks against, so this reads the index alone, which is the only place
    a stale row can be seen.
    """
    assert search(conn, "ordinance 2026-58") != []

    conn.execute("DELETE FROM segments WHERE transcript_id = ?", (sep22,))
    conn.commit()

    assert search(conn, "ordinance 2026-58") == []
    assert conn.execute("SELECT count(*) FROM segment_window_search").fetchone()[0] == 0


def test_two_mentions_two_lines_apart_stay_two_hits(
    conn: sqlite3.Connection, transcript: int
) -> None:
    """A window reaching from one mention to the next must not merge them."""
    speak(
        conn,
        transcript,
        (
            (0, 1_000, "the permit fee"),
            (1_000, 2_000, "was raised in july"),
            (2_000, 3_000, "the permit fee"),
            (3_000, 4_000, "was lowered again"),
        ),
    )

    hits = search(conn, "permit fee")

    assert [hit.start_ms for hit in hits] == [0, 2_000]


def test_every_word_of_a_query_must_be_there(conn: sqlite3.Connection, sep22: int) -> None:
    """A term that is nowhere in the transcript leaves the query with no hit."""
    assert search(conn, "ordinance aquifer") == []
    assert search(conn, "carries aquifer unanimously") == []
    # The same query without the absent word does find the split phrase, so the
    # two empty answers above are the absent word and not the fixture.
    assert len(search(conn, "carries unanimously")) == 1


def test_a_split_phrase_is_found_wherever_the_break_falls(
    conn: sqlite3.Connection, sep22: int
) -> None:
    """Two of the three namings of the ordinance cross a line, and both are found."""
    assert [hit.start_ms for hit in search(conn, "carries unanimously")] == [4_590_000]
    assert [hit.start_ms for hit in search(conn, "adopt ordinance")] == [4_575_000]
    assert [hit.start_ms for hit in search(conn, "permits")] == [4_233_000]


#: The caption track for the 2026-09-22 regular session, the meeting the
#: coordinator measured the bug on.
SEP22_FIXTURE_NAME = "jhsFsEz0P5A.en.srv3"

#: Where the file is looked for, in order. The first is the copy held by the
#: oversight repository next to this worktree.
SEP22_CANDIDATES = (
    Path(__file__).resolve().parents[3].parent
    / "townrecord-oversight"
    / "evidence"
    / "fixtures"
    / "youtube"
    / "captions-sep22"
    / SEP22_FIXTURE_NAME,
    Path(__file__).resolve().parents[3].parent
    / "townrecord-oversight"
    / "evidence"
    / "fixtures"
    / "youtube"
    / "captions"
    / SEP22_FIXTURE_NAME,
)


def sep22_fixture_path() -> Path | None:
    """Return the caption file of the September 22 meeting, or None.

    The file belongs to the machine, not to the repository, so the test skips
    when it is not there, and the skip message names every path that was probed.
    """
    named = os.environ.get("TOWNRECORD_SEP22_SRV3", "").strip()
    paths = (Path(named), *SEP22_CANDIDATES) if named else SEP22_CANDIDATES
    for path in paths:
        if path.is_file():
            return path
    return None


def test_the_real_september_22_transcript_answers_the_ordinance_query(
    conn: sqlite3.Connection, put_artifact: PutArtifact, capsys: pytest.CaptureFixture[str]
) -> None:
    """Search the three queries on the real file and report the seconds."""
    path = sep22_fixture_path()
    if path is None:
        probed = "\n".join(f"  {candidate}" for candidate in SEP22_CANDIDATES)
        pytest.skip(f"{SEP22_FIXTURE_NAME} was not found. Paths probed:\n{probed}")

    county = insert_jurisdiction(conn, type="county", name="Boulder County")
    city = insert_jurisdiction(conn, type="city", name="Longmont", parent_id=county)
    body = insert_body(conn, jurisdiction_id=city, name="City Council")
    source = insert_source(
        conn,
        jurisdiction_id=city,
        body_id=body,
        type="video_channel",
        origin="https://videos.test.invalid/channel/UC-longmont",
        suggested_by="discovery",
        reason="The fixture was captured from it.",
        status="accepted",
    )
    meeting = insert_meeting(
        conn,
        body_id=body,
        title="City Council Regular Session",
        starts_at="2026-09-22T19:00:00-06:00",
    )
    video = insert_video(
        conn,
        source_id=source,
        meeting_id=meeting,
        platform_video_id="jhsFsEz0P5A",
        is_primary=True,
    )
    raw = path.read_bytes()
    artifact = put_artifact("transcript", raw, "srv3")
    transcript = insert_transcript(
        conn, video_id=video, artifact_id=artifact, origin="publisher_captions"
    )
    segments = parse_srv3(raw)
    assert segments, f"{path} parsed to no segments"
    for segment in segments:
        insert_segment(
            conn,
            transcript_id=transcript,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            text=segment.text,
        )
    conn.commit()

    answered = {
        query: [(hit.start_ms, hit.end_ms, hit.excerpt) for hit in search(conn, query)]
        for query in (
            "ordinance 2026-58",
            "carries unanimously",
            "pass and adopt ordinance 2026-58",
        )
    }
    with capsys.disabled():
        print(f"\n{path.name}: {len(segments)} lines")
        for query, hits in answered.items():
            print(f'  "{query}": {len(hits)} hits')
            for start_ms, end_ms, excerpt in hits:
                print(f"    {start_ms / 1000:.3f}s to {end_ms / 1000:.3f}s  {excerpt}")

    # The coordinator's own read of this file put the third naming of the
    # ordinance at 4575 s and 4576 s, across a line break. A query for the
    # ordinance has to find it there, and the hit has to cite the lines it was
    # spoken between: the first line's start and the last line's end.
    ordinance = answered["ordinance 2026-58"]
    assert ordinance, "the ordinance is named in this meeting and the search found nothing"
    split = [hit for hit in ordinance if "pass and adopt ordinance" in hit[2]]
    assert split, ordinance
    start_ms, end_ms, excerpt = split[0]
    assert 4_575_000 <= start_ms < 4_576_000, split
    assert end_ms > start_ms, "a mention across two lines is cited at both of them"
    assert excerpt.endswith("2026-58."), split
    assert answered["pass and adopt ordinance 2026-58"], "the split phrase was not found"
    assert answered["carries unanimously"], "the vote was not found"
