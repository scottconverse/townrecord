"""SQLite connections with the pragmas the spec requires (spec 8.7, 14.3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: How long a connection waits for another writer before "database is locked".
#: Python's default is five seconds, and the live run of 2026-11-08 had writes
#: (a long transcript indexed for search) that held the file longer than that
#: while three workers and the schedule were all writing.
BUSY_TIMEOUT_S = 60.0


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a connection with write-ahead logging and foreign keys on.

    The connection runs in autocommit mode so that the migration runner can
    control its own transactions.
    """
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
