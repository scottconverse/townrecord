"""The two statements every repository module needs.

There is no ORM and no schema here. The table and column names are the
literals written in this package, never anything a caller sends, so the SQL
text is built from trusted names only.

The database is the source of truth (spec 8.7, decision 10 rule C), so nothing
in this package creates a table. The schema is migration
``0005_core_model.sql``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any


def insert(conn: sqlite3.Connection, table: str, values: Mapping[str, Any]) -> int:
    """Insert one row and return its id."""
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    cursor = conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values())
    )
    return int(cursor.lastrowid or 0)


def get(conn: sqlite3.Connection, table: str, row_id: int) -> sqlite3.Row | None:
    """Return one row by id, or None when there is no such row."""
    return conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
