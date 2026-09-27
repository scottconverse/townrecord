"""Meetings, their videos and their records.

Section 6.2 of the spec names the objects, section 7.2 step 7 says how two
recordings of one meeting are kept, and section 8.6 says a document is stored
as an artifact. A record never holds a path: it holds the artifact id.
"""

from __future__ import annotations

import sqlite3

from .rows import Meeting, Record, RecordPage, Video
from .store import get, insert


def insert_meeting(
    conn: sqlite3.Connection,
    *,
    body_id: int,
    starts_at: str,
    title: str | None = None,
    type: str = "regular",
    is_cancelled: bool = False,
    is_continued: bool = False,
) -> int:
    """Store one meeting and return its id.

    ``starts_at`` is the local start time as the body published it, ISO 8601,
    with the UTC offset when the source gives one.
    """
    return insert(
        conn,
        "meetings",
        {
            "body_id": body_id,
            "title": title,
            "starts_at": starts_at,
            "type": type,
            "is_cancelled": int(is_cancelled),
            "is_continued": int(is_continued),
        },
    )


def get_meeting(conn: sqlite3.Connection, meeting_id: int) -> Meeting | None:
    """Return the meeting, or None when there is no such row."""
    row = get(conn, "meetings", meeting_id)
    return None if row is None else Meeting.from_row(row)


def meeting_by_portal_id(
    conn: sqlite3.Connection, portal_source_id: int, portal_meeting_id: int
) -> Meeting | None:
    """Return the meeting a portal listed under that id, or None.

    This is the lookup a second sync of the same window does, and the partial
    unique index of migration 0009 is what makes it one row at most.
    """
    row = conn.execute(
        "SELECT * FROM meetings WHERE portal_source_id = ? AND portal_meeting_id = ?",
        (portal_source_id, portal_meeting_id),
    ).fetchone()
    return None if row is None else Meeting.from_row(row)


def upsert_meeting(
    conn: sqlite3.Connection,
    *,
    body_id: int,
    starts_at: str,
    title: str | None = None,
    type: str = "regular",
    is_cancelled: bool = False,
    portal_source_id: int | None = None,
    portal_meeting_id: int | None = None,
    portal_html_template_id: int | None = None,
) -> tuple[int, bool]:
    """Store a meeting a portal listed, or update the row a past sync made.

    The portal's own id finds the row first, and the body and start time
    second: a meeting another adapter stored is the same meeting, and the sync
    adopts it rather than writing a second row for one sitting.

    A cancellation is never cleared. The notice that cancelled a meeting is a
    fact about it, so a later listing that does not carry the marker leaves
    the flag where it was. A template id already known is kept when this
    listing has none.

    Returns the meeting id and whether the row was created.
    """
    found = None
    if portal_source_id is not None and portal_meeting_id is not None:
        found = meeting_by_portal_id(conn, portal_source_id, portal_meeting_id)
    if found is None:
        row = conn.execute(
            "SELECT * FROM meetings WHERE body_id = ? AND starts_at = ?", (body_id, starts_at)
        ).fetchone()
        found = None if row is None else Meeting.from_row(row)
    if found is None:
        created = insert_meeting(
            conn,
            body_id=body_id,
            starts_at=starts_at,
            title=title,
            type=type,
            is_cancelled=is_cancelled,
        )
        # The portal columns are written here rather than through insert_meeting,
        # which stores a meeting and knows nothing about portals. A row a portal
        # listed must carry the ids it was listed under, or a download cannot be
        # cited back to the portal that published it (spec 9.2).
        conn.execute(
            "UPDATE meetings SET portal_source_id = ?, portal_meeting_id = ?, "
            "portal_html_template_id = ? WHERE id = ?",
            (portal_source_id, portal_meeting_id, portal_html_template_id, created),
        )
        return created, True
    conn.execute(
        "UPDATE meetings SET title = ?, type = ?, is_cancelled = ?, "
        "portal_source_id = ?, portal_meeting_id = ?, portal_html_template_id = ? "
        "WHERE id = ?",
        (
            title,
            type,
            int(found.is_cancelled or is_cancelled),
            portal_source_id,
            portal_meeting_id,
            found.portal_html_template_id
            if portal_html_template_id is None
            else portal_html_template_id,
            found.id,
        ),
    )
    return found.id, False


def insert_video(
    conn: sqlite3.Connection,
    *,
    source_id: int,
    platform_video_id: str,
    meeting_id: int | None = None,
    title: str | None = None,
    url: str | None = None,
    published_at: str | None = None,
    duration_s: int | None = None,
    is_primary: bool = False,
    capture_state: str = "pending",
    readiness: str = "unknown",
    source_note: str | None = None,
) -> int:
    """Store one listed video and return its id (spec 7.2 step 7).

    A second primary video for one meeting is refused by the partial unique
    index in the migration, which is the rule the spec states.
    """
    return insert(
        conn,
        "videos",
        {
            "source_id": source_id,
            "meeting_id": meeting_id,
            "platform_video_id": platform_video_id,
            "title": title,
            "url": url,
            "published_at": published_at,
            "duration_s": duration_s,
            "is_primary": int(is_primary),
            "capture_state": capture_state,
            "readiness": readiness,
            "source_note": source_note,
        },
    )


def get_video(conn: sqlite3.Connection, video_id: int) -> Video | None:
    """Return the video, or None when there is no such row."""
    row = get(conn, "videos", video_id)
    return None if row is None else Video.from_row(row)


def videos_of_platform(conn: sqlite3.Connection, platform_video_id: str) -> list[Video]:
    """Return every video row with that platform id, oldest row first.

    More than one row can exist when two sources list the same platform video,
    which is why this returns a list and lets the caller choose. The caller
    that knows about watched channels is the one that can choose well.
    """
    rows = conn.execute(
        "SELECT * FROM videos WHERE platform_video_id = ? ORDER BY id", (platform_video_id,)
    ).fetchall()
    return [Video.from_row(row) for row in rows]


def attach_video(
    conn: sqlite3.Connection,
    video_id: int,
    *,
    meeting_id: int,
    is_primary: bool = True,
    url: str | None = None,
) -> None:
    """Fill in what a video row was missing. Nothing already said changes.

    The title, the source and the capture state stay as they are: a row a
    watched channel made is that channel's row, and a listing that carries the
    same platform video id never renames it (spec 7.2 step 7). What a listing
    can add is the meeting the video belongs to, the primary flag, and the URL
    when the row had none.
    """
    conn.execute(
        "UPDATE videos SET meeting_id = COALESCE(meeting_id, ?), "
        "url = COALESCE(url, ?), "
        "is_primary = CASE WHEN ? = 1 THEN 1 ELSE is_primary END "
        "WHERE id = ?",
        (meeting_id, url, int(is_primary), video_id),
    )


def primary_video(conn: sqlite3.Connection, meeting_id: int) -> Video | None:
    """Return the primary video of a meeting, or None when it has none."""
    row = conn.execute(
        "SELECT * FROM videos WHERE meeting_id = ? AND is_primary = 1", (meeting_id,)
    ).fetchone()
    return None if row is None else Video.from_row(row)


def insert_record(
    conn: sqlite3.Connection,
    *,
    meeting_id: int,
    kind: str,
    artifact_id: int,
    source_id: int | None = None,
    title: str | None = None,
    page_count: int | None = None,
    portal_document_id: int | None = None,
    portal_template_id: int | None = None,
) -> int:
    """Store one document of a meeting and return its id.

    ``artifact_id`` is the content-addressed file from migration 0004. A record
    never holds a raw path. The two portal ids are the ids the portal published
    the document under, which is what a citation of it names (spec 9.2).
    """
    return insert(
        conn,
        "records",
        {
            "meeting_id": meeting_id,
            "source_id": source_id,
            "kind": kind,
            "title": title,
            "artifact_id": artifact_id,
            "page_count": page_count,
            "portal_document_id": portal_document_id,
            "portal_template_id": portal_template_id,
        },
    )


def get_record(conn: sqlite3.Connection, record_id: int) -> Record | None:
    """Return the record, or None when there is no such row."""
    row = get(conn, "records", record_id)
    return None if row is None else Record.from_row(row)


def record_for_portal_document(
    conn: sqlite3.Connection, meeting_id: int, portal_document_id: int
) -> Record | None:
    """Return the record a portal document was stored as, or None.

    This is the lookup that makes a second download of the same document a
    no-op rather than a second row (spec 9.2).
    """
    row = conn.execute(
        "SELECT * FROM records WHERE meeting_id = ? AND portal_document_id = ?",
        (meeting_id, portal_document_id),
    ).fetchone()
    return None if row is None else Record.from_row(row)


def records_of_meeting(conn: sqlite3.Connection, meeting_id: int) -> list[Record]:
    """Return the documents of a meeting, in id order."""
    rows = conn.execute(
        "SELECT * FROM records WHERE meeting_id = ? ORDER BY id", (meeting_id,)
    ).fetchall()
    return [Record.from_row(row) for row in rows]


def insert_record_page(
    conn: sqlite3.Connection,
    *,
    record_id: int,
    page_number: int,
    text: str = "",
    footer_page_number: int | None = None,
) -> int:
    """Store one page of a record and return its id (spec 9.6)."""
    return insert(
        conn,
        "record_pages",
        {
            "record_id": record_id,
            "page_number": page_number,
            "footer_page_number": footer_page_number,
            "text": text,
        },
    )


def get_record_page(conn: sqlite3.Connection, record_page_id: int) -> RecordPage | None:
    """Return the page, or None when there is no such row."""
    row = get(conn, "record_pages", record_page_id)
    return None if row is None else RecordPage.from_row(row)


def meetings_of_body(
    conn: sqlite3.Connection,
    body_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[Meeting]:
    """Return the meetings of a body, earliest first.

    ``date_from`` and ``date_to`` are local dates as YYYY-MM-DD and are
    inclusive. The date a meeting falls on is the local date it was published
    with, which is the first ten characters of ``starts_at``, so nothing is
    converted to UTC (spec 16.2).
    """
    sql = ["SELECT * FROM meetings WHERE body_id = ?"]
    params: list[object] = [body_id]
    if date_from is not None:
        sql.append("AND substr(starts_at, 1, 10) >= ?")
        params.append(date_from)
    if date_to is not None:
        sql.append("AND substr(starts_at, 1, 10) <= ?")
        params.append(date_to)
    sql.append("ORDER BY starts_at, id")
    return [Meeting.from_row(row) for row in conn.execute("\n".join(sql), tuple(params))]


def record_pages(conn: sqlite3.Connection, record_id: int) -> list[RecordPage]:
    """Return the pages of a record, in page order."""
    rows = conn.execute(
        "SELECT * FROM record_pages WHERE record_id = ? ORDER BY page_number", (record_id,)
    ).fetchall()
    return [RecordPage.from_row(row) for row in rows]
