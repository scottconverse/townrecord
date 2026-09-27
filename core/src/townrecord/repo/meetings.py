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
        },
    )


def get_video(conn: sqlite3.Connection, video_id: int) -> Video | None:
    """Return the video, or None when there is no such row."""
    row = get(conn, "videos", video_id)
    return None if row is None else Video.from_row(row)


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
) -> int:
    """Store one document of a meeting and return its id.

    ``artifact_id`` is the content-addressed file from migration 0004. A record
    never holds a raw path.
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
        },
    )


def get_record(conn: sqlite3.Connection, record_id: int) -> Record | None:
    """Return the record, or None when there is no such row."""
    row = get(conn, "records", record_id)
    return None if row is None else Record.from_row(row)


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


def record_pages(conn: sqlite3.Connection, record_id: int) -> list[RecordPage]:
    """Return the pages of a record, in page order."""
    rows = conn.execute(
        "SELECT * FROM record_pages WHERE record_id = ? ORDER BY page_number", (record_id,)
    ).fetchall()
    return [RecordPage.from_row(row) for row in rows]
