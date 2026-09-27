"""Prove this Python's SQLite has FTS5 (spec 14.3, spec 12.4).

The proof is a real virtual table: create it, insert rows, and query it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from townrecord.db import connect


def test_sqlite_reports_the_fts5_compile_option() -> None:
    conn = sqlite3.connect(":memory:")
    try:
        options = {row[0] for row in conn.execute("PRAGMA compile_options")}
    finally:
        conn.close()
    assert "ENABLE_FTS5" in options, f"FTS5 is not compiled in. Options: {sorted(options)}"


def test_a_virtual_table_can_be_created_and_queried(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.execute("CREATE VIRTUAL TABLE segment_search USING fts5(body)")
        rows = [
            ("the council approved ordinance 2026-58",),
            ("a study session on water rates",),
            ("the planning commission heard a rezoning request",),
        ]
        conn.executemany("INSERT INTO segment_search (body) VALUES (?)", rows)
        found = conn.execute(
            "SELECT body FROM segment_search WHERE segment_search MATCH ? ORDER BY rank",
            ("water OR ordinance",),
        ).fetchall()
        assert len(found) == 2
        assert any("water rates" in row["body"] for row in found)
        assert any("2026-58" in row["body"] for row in found)
    finally:
        conn.close()
