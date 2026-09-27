"""The yt-dlp download archive, written from the database (spec 8.7).

"The database is the source of truth. Regenerate the yt-dlp --download-archive
file from the database before each capture, because a stale archive file can
hide a meeting that was never captured."

So the file is never read back and never edited. It is written whole, from the
rows that say a video has a transcript, before every run. A file left over from
an earlier run cannot keep a video out of this one.

The format is yt-dlp's own: one line per video, ``<extractor> <video id>``,
separated by a single space. The extractor key is taken from the origin of the
source the video was listed on, so a host TownRecord does not know is left out
of the file rather than guessed at. Leaving a line out can only make yt-dlp
fetch something again; putting a wrong line in would hide a meeting, which is
the failure the spec names.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

#: The hosts whose videos yt-dlp reports under its `youtube` extractor.
YOUTUBE_HOSTS: frozenset[str] = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
    }
)

#: The file name inside the work folder of the storage root.
ARCHIVE_NAME = "download-archive.txt"


def extractor_for_origin(origin: str | None) -> str | None:
    """Return the yt-dlp extractor key for a source origin, or None.

    None means TownRecord does not know this host, and a line it guessed at
    could hide a meeting, so no line is written.
    """
    if not origin:
        return None
    text = origin.strip()
    if "://" not in text:
        return None
    host = urlsplit(text).hostname or ""
    return "youtube" if host.lower() in YOUTUBE_HOSTS else None


def archive_path(storage_root: str | Path) -> Path:
    """Return the archive file under the storage root."""
    return Path(storage_root) / "work" / ARCHIVE_NAME


def captured_lines(conn: sqlite3.Connection) -> list[str]:
    """Return the archive lines for every video that has a transcript.

    A video counts as captured when a transcript row points at it, which is the
    same fact the rest of the system reads. Nothing here looks at a file on
    disk or at `capture_state`: a state word can be stale, a row cannot.
    """
    rows = conn.execute(
        "SELECT DISTINCT sources.origin, videos.platform_video_id "
        "FROM transcripts "
        "JOIN videos ON videos.id = transcripts.video_id "
        "JOIN sources ON sources.id = videos.source_id "
        "ORDER BY videos.platform_video_id"
    ).fetchall()
    lines: list[str] = []
    for row in rows:
        extractor = extractor_for_origin(row["origin"])
        if extractor is None:
            continue
        lines.append(f"{extractor} {row['platform_video_id']}")
    return lines


def write(conn: sqlite3.Connection, storage_root: str | Path) -> tuple[Path, int]:
    """Write the archive for this capture and return its path and line count.

    The file is replaced, not appended to, so what is on disk after this call
    is exactly what the database says.
    """
    path = archive_path(storage_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = captured_lines(conn)
    text = "".join(f"{line}\n" for line in lines)
    path.write_text(text, encoding="utf-8")
    return path, len(lines)
