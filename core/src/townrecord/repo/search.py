"""Full-text search over transcript lines and record pages (spec 12.4).

Two indexes are searched, one per kind of text: the spoken lines of a
transcript and the text of a record page (``record_page_search``). Both are
FTS5 virtual tables kept in step by triggers, so nothing here maintains an
index by hand. The page index is migration ``0007_search.sql``; the lines are
read from ``segment_window_search`` of migration ``0008_segment_windows.sql``.

Why the lines are read from a window index and not from ``segment_search``. A
caption line is five to eight words, so a phrase the user types often crosses a
line break, and FTS5 matches inside one row. ``segment_window_search`` holds
every run of one, two or three consecutive lines as its own row, so a phrase of
up to three lines is again a phrase inside a single row. A window of one line
is a line, so the window index answers for a single-word query too, and reading
one index is what keeps bm25 scores comparable: the score depends on the size
and the contents of the index it comes from, and two indexes cannot be ranked
against each other. Every line of ``segments`` is in that one index, and
``segment_search`` from 0007 stays as it shipped.

One spoken mention is one hit. A phrase that fits on one line matches every
window that contains that line, and the windows overlap, so the matches are
de-duplicated: the tightest window that holds the match is kept and the longer
ones that hold the same lines are dropped. Two mentions two lines apart stay
two hits, because their windows do not share a line.

The index is not comparable across the two tables either: the two lists are
joined segment hits first. The whole list is then cut at the limit.

User text is data, never syntax. A query is split into words and every word is
written as its own quoted phrase, so a string that happens to contain FTS5
operators (``AND``, ``OR``, ``NOT``, ``NEAR``, ``*``, ``^``, ``:``, ``-``, a
quote, a parenthesis) is searched for as those words and cannot raise. A query
that holds no word at all (for example ``""`` or ``***``) matches nothing.

All the words must be present, and a hit that holds them next to each other one
after another is ranked before a hit that holds them apart: the same quoted
words are asked once more as a single phrase, and every window that answers
that question comes first.

Every hit carries a citation (spec 10.5). A video citation carries the video
id, the seconds, the verbatim excerpt, the SHA-256 of the transcript artifact
and the transcript's origin. A record citation carries the source, the
meeting, the document, the page number, the excerpt and the document's
SHA-256.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

#: The scope of a hit: one spoken line, or one page of a document.
SEGMENT = "segment"
RECORD_PAGE = "record_page"

#: How many tokens of a page the excerpt shows, and what stands between the
#: parts of it. A segment is one line of speech, so its excerpt is the whole
#: line and no window is taken.
_EXCERPT_TOKENS = 12
_EXCERPT_ELLIPSIS = " ... "

#: One word of a query. Letters, digits and the alphanumeric characters of
#: other scripts; the underscore is left out, because FTS5's unicode61
#: tokenizer treats it as a separator rather than as a token character.
_WORD = re.compile(r"[^\W_]+", re.UNICODE)

#: The window a line of speech is indexed in is its own line plus the ones
#: before it, up to this many lines. Three is what migration 0008 writes down:
#: the measured break is one line wide, and a third line is the margin.
_WINDOW_LINES = 3

#: The windows one mention can sit in. A line ends one window of each length,
#: and a window that holds it but ends later reaches back over it: one window
#: of one line, two of two lines and three of three lines, so six. The query
#: reads this many times the limit before de-duplicating, so that the longer
#: windows of a mention that is already found cannot crowd a later mention out
#: of the answer.
_WINDOWS_PER_MENTION = _WINDOW_LINES * (_WINDOW_LINES + 1) // 2


@dataclass(frozen=True)
class SearchCitation:
    """The evidence behind one hit (spec 10.5).

    One shape per kind. ``kind`` is 'video' or 'record', and it says which
    half is filled. The SHA-256 is read from the artifact row rather than
    copied into anything: an artifact never changes, so its row is the hash.
    """

    kind: str
    excerpt: str
    meeting_id: int | None = None
    # Video: the id the publisher gave the video, the seconds it covers, the
    # transcript it was read from, and where that transcript came from.
    video_id: int | None = None
    platform_video_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    transcript_id: int | None = None
    transcript_artifact_sha256: str | None = None
    transcript_origin: str | None = None
    # Record: the document, the page, and the source the document came from.
    record_id: int | None = None
    source_id: int | None = None
    source_origin: str | None = None
    page_number: int | None = None
    document_artifact_sha256: str | None = None


@dataclass(frozen=True)
class SearchHit:
    """One line of speech or one page of a document that matched."""

    scope: str
    excerpt: str
    # What the hit rests on, filled for every hit: a hit without its evidence
    # is not a citation of anything (spec 10.5). It comes before the optional
    # fields because it is not optional.
    citation: SearchCitation
    # A segment hit carries its seconds and its speaker label; a page hit
    # carries its page number. The other half is None.
    start_ms: int | None = None
    end_ms: int | None = None
    speaker_label: str | None = None
    page_number: int | None = None
    footer_page_number: int | None = None
    # Where the hit sits in the area. A video that is not matched to a meeting
    # yet (spec 7.2 step 7) leaves the meeting, the body and the level empty.
    meeting_id: int | None = None
    meeting_title: str | None = None
    starts_at: str | None = None
    body_id: int | None = None
    body_name: str | None = None
    jurisdiction_id: int | None = None
    jurisdiction_name: str | None = None
    level: str | None = None


def query_terms(q: str) -> list[str]:
    """Split a user query into the words that are searched for.

    Anything that is not a letter or a digit separates words, so an FTS5
    operator is returned as the plain word it looks like, and a query of pure
    punctuation returns nothing at all.
    """
    return _WORD.findall(q or "")


def match_expression(q: str) -> str | None:
    """Build an FTS5 MATCH expression from user text, or None for no words.

    Every word is quoted, so the expression holds no FTS5 operator and no
    caller can write one. A word is searched with AND against the others: a
    hit holds all of them.
    """
    terms = query_terms(q)
    if not terms:
        return None
    return " ".join('"' + term + '"' for term in terms)


def phrase_expression(q: str) -> str | None:
    """Build the whole of user text as one FTS5 phrase, or None for no words.

    The words of the query are joined by a space inside one pair of quotes, so
    what the user typed is asked for as the phrase it looks like: the words in
    the order they were written, next to each other. A word of a query holds
    letters and digits only, so no character of a phrase can close its quote
    early and no caller can write an FTS5 operator here. The phrase is asked
    for only to rank: a hit that answers it is a closer answer than one that
    holds the same words scattered across its lines.
    """
    terms = query_terms(q)
    if not terms:
        return None
    return '"' + " ".join(terms) + '"'


def search(
    conn: sqlite3.Connection,
    q: str,
    level: str | None = None,
    body: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    type: str | None = None,
    limit: int = 50,
) -> list[SearchHit]:
    """Search transcript lines and record pages and return the hits (spec 12.4).

    Args:
        conn: An open connection to a migrated database.
        q: The text the user typed. It is data: it can hold FTS5 operators and
            still only ever searches for words.
        level: A jurisdiction type (spec 6.1), for example 'city'. A hit whose
            video is not matched to a meeting has no level and is left out.
        body: A body id. A hit whose video is not matched to a meeting is left
            out, because nothing then says which body spoke.
        date_from: The earliest meeting date, inclusive, as YYYY-MM-DD. The
            date is the local date the meeting starts, as published.
        date_to: The latest meeting date, inclusive, as YYYY-MM-DD.
        type: A record kind (agenda, packet, minutes, ordinance, resolution,
            budget, report). It filters to documents: when it is given, only
            page hits of that kind are returned and no line of speech is.
        limit: The most hits to return. A value below zero is treated as zero.

    Returns:
        The hits, segment hits before page hits, each group best match first,
        and the whole list cut at the limit.
    """
    if limit <= 0:
        return []
    expression = match_expression(q)
    if expression is None:
        return []
    segment_hits = (
        []
        if type is not None
        else _segment_hits(
            conn, expression, phrase_expression(q), level, body, date_from, date_to, limit
        )
    )
    page_hits = _page_hits(conn, expression, level, body, date_from, date_to, type, limit)
    return (segment_hits + page_hits)[:limit]


def _segment_hits(
    conn: sqlite3.Connection,
    expression: str,
    phrase: str | None,
    level: str | None,
    body: int | None,
    date_from: str | None,
    date_to: str | None,
    limit: int,
) -> list[SearchHit]:
    """Search the spoken lines of the transcripts, one hit per mention.

    The index holds windows of one, two and three lines, so the lines of a hit
    are read from ``segments``: the window's first and last line give the
    seconds it covers and the excerpt is the verbatim text of the lines in
    between, never the copy of the text the index keeps for matching.
    """
    sql = [
        "SELECT segment_window_search.rowid AS window_rowid,",
        "       (segment_window_search.rowid - segment_window_search.last_segment_id * 4)",
        "           AS span,",
        "       bm25(segment_window_search) AS score,",
        "       segment_window_search.transcript_id AS transcript_id,",
        "       segment_window_search.first_segment_id AS first_segment_id,",
        "       segment_window_search.last_segment_id AS last_segment_id,",
        "       first_line.start_ms AS start_ms, last_line.end_ms AS end_ms,",
        "       first_line.text AS first_text, middle.text AS middle_text,",
        "       last_line.text AS last_text,",
        "       first_line.speaker_label AS speaker_label,",
        "       transcripts.origin AS transcript_origin,",
        "       artifacts.sha256 AS transcript_sha256,",
        "       videos.id AS video_id, videos.platform_video_id,",
        "       meetings.id AS meeting_id, meetings.title AS meeting_title,",
        "       meetings.starts_at,",
        "       bodies.id AS body_id, bodies.name AS body_name,",
        "       jurisdictions.id AS jurisdiction_id, jurisdictions.name AS jurisdiction_name,",
        "       jurisdictions.type AS level",
        "FROM segment_window_search",
        "JOIN segments AS first_line",
        "  ON first_line.id = segment_window_search.first_segment_id",
        "JOIN segments AS last_line",
        "  ON last_line.id = segment_window_search.last_segment_id",
        # The line between the two ends of a window of three lines, and no row
        # at all for a window of one or two: nothing lies strictly between the
        # first and the last of those.
        "LEFT JOIN segments AS middle",
        "  ON middle.transcript_id = segment_window_search.transcript_id",
        " AND (middle.start_ms > first_line.start_ms",
        "      OR (middle.start_ms = first_line.start_ms AND middle.id > first_line.id))",
        " AND (middle.start_ms < last_line.start_ms",
        "      OR (middle.start_ms = last_line.start_ms AND middle.id < last_line.id))",
        "JOIN transcripts ON transcripts.id = segment_window_search.transcript_id",
        "JOIN artifacts ON artifacts.id = transcripts.artifact_id",
        "JOIN videos ON videos.id = transcripts.video_id",
        # A video is listed before it is matched to a meeting (spec 7.2 step
        # 7), so the rest of the walk is a left join: a line of an unmatched
        # video is still a line of speech, and a filter on the area is what
        # leaves it out.
        "LEFT JOIN meetings ON meetings.id = videos.meeting_id",
        "LEFT JOIN bodies ON bodies.id = meetings.body_id",
        "LEFT JOIN jurisdictions ON jurisdictions.id = bodies.jurisdiction_id",
        "WHERE segment_window_search MATCH ?",
    ]
    params: list[object] = [expression]
    sql, params = _area_filters(sql, params, level, body, date_from, date_to, "jurisdictions.type")
    sql.append("ORDER BY score LIMIT ?")
    params.append(limit * _WINDOWS_PER_MENTION)
    rows = conn.execute("\n".join(sql), tuple(params)).fetchall()
    return [
        _segment_hit(row) for row in _one_hit_per_mention(conn, rows, _phrase_hits(conn, phrase))
    ]


def _phrase_hits(conn: sqlite3.Connection, phrase: str | None) -> set[int]:
    """Return the window rows that hold the whole query as one phrase.

    The phrases are asked for by rowid alone, so the answer is cheap to get and
    is used only to rank, never to add or drop a hit: a phrase is a set of
    words next to each other, and a window that holds it holds all of them.
    """
    if phrase is None:
        return set()
    return {
        int(row["rowid"])
        for row in conn.execute(
            "SELECT rowid FROM segment_window_search WHERE segment_window_search MATCH ?",
            (phrase,),
        )
    }


def _line_positions(conn: sqlite3.Connection, transcript_id: int) -> dict[int, int]:
    """Return where each line of a transcript sits in its time order.

    Zero for the first line. This is the order the windows are cut in, so two
    windows belong to the same mention when their runs of positions overlap.
    """
    rows = conn.execute(
        "SELECT id FROM segments WHERE transcript_id = ? ORDER BY start_ms, id",
        (transcript_id,),
    ).fetchall()
    return {int(row["id"]): place for place, row in enumerate(rows)}


def _one_hit_per_mention(
    conn: sqlite3.Connection, rows: list[sqlite3.Row], phrase_hits: set[int]
) -> list[sqlite3.Row]:
    """Keep one window of each spoken mention and drop the rest.

    A mention is a run of consecutive lines the query matched. Every window
    that holds it matched too, and the windows overlap, so a mention that fits
    on one line would otherwise be reported once per window over it.

    The window kept is the tightest one: the one that names the fewest lines
    that did not have to be there. So windows are taken shortest first, and a
    window that holds the query as one phrase comes before one that only holds
    its words, which is what puts an exact phrase first in the answer. A window
    that shares a line with one already taken is the same mention and is
    dropped; two mentions two lines apart share no line and stay two hits.
    """
    positions: dict[int, dict[int, int]] = {}
    places: dict[int, tuple[int, int]] = {}

    def place(row: sqlite3.Row) -> tuple[int, int]:
        window_rowid = int(row["window_rowid"])
        if window_rowid not in places:
            transcript_id = int(row["transcript_id"])
            if transcript_id not in positions:
                positions[transcript_id] = _line_positions(conn, transcript_id)
            here = positions[transcript_id]
            places[window_rowid] = (
                here[int(row["first_segment_id"])],
                here[int(row["last_segment_id"])],
            )
        return places[window_rowid]

    def tightest_first(row: sqlite3.Row) -> tuple[int, int, float, int]:
        first, _last = place(row)
        return (
            int(row["window_rowid"]) not in phrase_hits,
            int(row["span"]),
            float(row["score"]),
            first,
        )

    kept: list[sqlite3.Row] = []
    taken: list[tuple[int, int, int]] = []
    for row in sorted(rows, key=tightest_first):
        first, last = place(row)
        transcript_id = int(row["transcript_id"])
        if any(
            held == transcript_id and first <= held_last and held_first <= last
            for held, held_first, held_last in taken
        ):
            continue
        kept.append(row)
        taken.append((transcript_id, first, last))
    return kept


def _segment_hit(row: sqlite3.Row) -> SearchHit:
    """Build the hit of one window from the lines it was found in.

    The excerpt is the verbatim text of the window's lines, joined the way the
    index joined them, and the seconds are the start of its first line and the
    end of its last: a phrase that crosses a line break is cited at both lines
    it was spoken between.
    """
    span = int(row["span"])
    excerpt = " ".join(
        part
        for part in (
            str(row["first_text"]),
            None if span < _WINDOW_LINES else row["middle_text"],
            None if span < 2 else str(row["last_text"]),
        )
        if part is not None
    )
    start_ms = int(row["start_ms"])
    end_ms = int(row["end_ms"])
    return SearchHit(
        scope=SEGMENT,
        excerpt=excerpt,
        start_ms=start_ms,
        end_ms=end_ms,
        speaker_label=row["speaker_label"],
        meeting_id=row["meeting_id"],
        meeting_title=row["meeting_title"],
        starts_at=row["starts_at"],
        body_id=row["body_id"],
        body_name=row["body_name"],
        jurisdiction_id=row["jurisdiction_id"],
        jurisdiction_name=row["jurisdiction_name"],
        level=row["level"],
        citation=SearchCitation(
            kind="video",
            excerpt=excerpt,
            meeting_id=row["meeting_id"],
            video_id=int(row["video_id"]),
            platform_video_id=str(row["platform_video_id"]),
            start_ms=start_ms,
            end_ms=end_ms,
            transcript_id=int(row["transcript_id"]),
            transcript_artifact_sha256=str(row["transcript_sha256"]),
            transcript_origin=str(row["transcript_origin"]),
        ),
    )


def _page_hits(
    conn: sqlite3.Connection,
    expression: str,
    level: str | None,
    body: int | None,
    date_from: str | None,
    date_to: str | None,
    type: str | None,
    limit: int,
) -> list[SearchHit]:
    """Search the text of the record pages."""
    sql = [
        "SELECT record_pages.id AS page_id, record_pages.page_number,",
        "       record_pages.footer_page_number,",
        f"       snippet(record_page_search, 0, '', '', '{_EXCERPT_ELLIPSIS}', {_EXCERPT_TOKENS})",
        "           AS excerpt,",
        "       records.id AS record_id, records.kind,",
        "       records.source_id, sources.origin AS source_origin,",
        "       documents.sha256 AS document_sha256,",
        "       meetings.id AS meeting_id, meetings.title AS meeting_title,",
        "       meetings.starts_at,",
        "       bodies.id AS body_id, bodies.name AS body_name,",
        "       jurisdictions.id AS jurisdiction_id, jurisdictions.name AS jurisdiction_name,",
        "       jurisdictions.type AS level",
        "FROM record_page_search",
        "JOIN record_pages ON record_pages.id = record_page_search.rowid",
        "JOIN records ON records.id = record_pages.record_id",
        "JOIN artifacts AS documents ON documents.id = records.artifact_id",
        "LEFT JOIN sources ON sources.id = records.source_id",
        "JOIN meetings ON meetings.id = records.meeting_id",
        "JOIN bodies ON bodies.id = meetings.body_id",
        "JOIN jurisdictions ON jurisdictions.id = bodies.jurisdiction_id",
        "WHERE record_page_search MATCH ?",
    ]
    params: list[object] = [expression]
    if type is not None:
        sql.append("AND records.kind = ?")
        params.append(type)
    # A record always has a meeting (the column is NOT NULL), so the body
    # filter is safe here without a left join.
    sql, params = _area_filters(sql, params, level, body, date_from, date_to, "jurisdictions.type")
    sql.append("ORDER BY rank LIMIT ?")
    params.append(limit)
    rows = conn.execute("\n".join(sql), tuple(params)).fetchall()
    return [
        SearchHit(
            scope=RECORD_PAGE,
            excerpt=str(row["excerpt"]),
            page_number=int(row["page_number"]),
            footer_page_number=row["footer_page_number"],
            meeting_id=int(row["meeting_id"]),
            meeting_title=row["meeting_title"],
            starts_at=row["starts_at"],
            body_id=int(row["body_id"]),
            body_name=str(row["body_name"]),
            jurisdiction_id=int(row["jurisdiction_id"]),
            jurisdiction_name=str(row["jurisdiction_name"]),
            level=str(row["level"]),
            citation=SearchCitation(
                kind="record",
                excerpt=str(row["excerpt"]),
                meeting_id=int(row["meeting_id"]),
                record_id=int(row["record_id"]),
                source_id=row["source_id"],
                source_origin=row["source_origin"],
                page_number=int(row["page_number"]),
                document_artifact_sha256=str(row["document_sha256"]),
            ),
        )
        for row in rows
    ]


def _area_filters(
    sql: list[str],
    params: list[object],
    level: str | None,
    body: int | None,
    date_from: str | None,
    date_to: str | None,
    level_column: str,
) -> tuple[list[str], list[object]]:
    """Add the area filters that were given to a query.

    The meeting date is the local date as published, so it is compared on the
    first ten characters of ``starts_at`` and never converted to UTC (spec
    16.2, and the rule the core model writes down).
    """
    if level is not None:
        sql.append(f"AND {level_column} = ?")
        params.append(level)
    if body is not None:
        sql.append("AND bodies.id = ?")
        params.append(body)
    if date_from is not None:
        sql.append("AND substr(meetings.starts_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to is not None:
        sql.append("AND substr(meetings.starts_at, 1, 10) <= ?")
        params.append(date_to)
    return sql, params
