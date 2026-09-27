"""SQLite connections with the pragmas the spec requires (spec 8.7, 14.3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with write-ahead logging and foreign keys on.

    The connection runs in autocommit mode so that the migration runner can
    control its own transactions.
    """
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
