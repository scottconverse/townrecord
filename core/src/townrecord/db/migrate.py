"""Numbered SQL migrations, applied once each, in order (spec 14.3).

A migration file is applied inside one transaction that also records the
version in `schema_migrations`. If any statement fails, the transaction is
rolled back and the database is left exactly as it was.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

#: Where the numbered SQL files live.
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

#: `0001_jobs.sql` and similar.
FILE_PATTERN = re.compile(r"^(\d{4})_([A-Za-z0-9_]+)\.sql$")

#: The bookkeeping table. It is not a schema change, so it is not itself a migration.
SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


def migrations_dir() -> Path:
    """Return the folder that holds the migration files."""
    return MIGRATIONS_DIR


def discover(directory: Path | None = None) -> list[tuple[int, str, Path]]:
    """Return every migration as (version, name, path), ordered by version."""
    folder = MIGRATIONS_DIR if directory is None else Path(directory)
    found: list[tuple[int, str, Path]] = []
    seen: dict[int, str] = {}
    for path in sorted(folder.iterdir()):
        if path.suffix != ".sql":
            continue
        match = FILE_PATTERN.match(path.name)
        if match is None:
            raise ValueError(f"Migration file name is not <number>_<name>.sql: {path.name}")
        version = int(match.group(1))
        if version in seen:
            raise ValueError(
                f"Two migration files use version {version}: {seen[version]}, {path.name}"
            )
        seen[version] = path.name
        found.append((version, match.group(2), path))
    found.sort(key=lambda item: item[0])
    return found


def split_statements(sql: str) -> list[str]:
    """Split a SQL script into statements, honoring string literals and comments."""
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            if buffer.strip():
                statements.append(buffer)
            buffer = ""
    if buffer.strip():
        statements.append(buffer)
    return statements


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return the versions already recorded as applied."""
    conn.execute(SCHEMA_MIGRATIONS_DDL)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {int(row[0]) for row in rows}


def migrate(conn: sqlite3.Connection, directory: Path | None = None) -> list[int]:
    """Apply every migration that has not run yet. Return the versions applied."""
    done = applied_versions(conn)
    applied: list[int] = []
    for version, name, path in discover(directory):
        if version in done:
            continue
        _apply_one(conn, version, name, path)
        applied.append(version)
    return applied


def _apply_one(conn: sqlite3.Connection, version: int, name: str, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    statements = split_statements(sql)
    conn.execute("BEGIN")
    try:
        for statement in statements:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (version, name, datetime.now(UTC).isoformat()),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
