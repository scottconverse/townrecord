"""The migration system (spec 14.3) and the jobs table (spec 16.1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from townrecord.db import applied_versions, connect, discover, migrate, split_statements


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def test_the_shipped_migrations_are_numbered_in_order() -> None:
    found = discover()
    assert [version for version, _, _ in found] == sorted(version for version, _, _ in found)
    assert [version for version, _, _ in found] == [1, 2, 3]


def test_migrate_applies_every_migration(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        applied = migrate(conn)
        assert applied == [1, 2, 3]
        assert applied_versions(conn) == {1, 2, 3}
    finally:
        conn.close()


def test_migrate_twice_applies_nothing_the_second_time(conn: sqlite3.Connection) -> None:
    assert migrate(conn) == []


def test_the_jobs_table_has_the_columns_of_spec_16_1(conn: sqlite3.Connection) -> None:
    columns = column_names(conn, "jobs")
    for expected in (
        "claim_token",
        "heartbeat_at",
        "checkpoint",
        "lane",
        "state",
        "attempts",
        "last_error",
        "created_at",
        "started_at",
        "finished_at",
    ):
        assert expected in columns, f"jobs is missing {expected}"


def test_a_job_accepts_the_two_lanes_and_rejects_others(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO jobs (kind, lane) VALUES ('scan', 'heavy')")
    conn.execute("INSERT INTO jobs (kind, lane) VALUES ('scan', 'normal')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO jobs (kind, lane) VALUES ('scan', 'editorial')")


def test_a_job_accepts_every_state_of_spec_16_1(conn: sqlite3.Connection) -> None:
    for state in ("queued", "running", "done", "failed", "paused"):
        conn.execute("INSERT INTO jobs (kind, state) VALUES ('scan', ?)", (state,))


def test_a_job_state_outside_the_list_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO jobs (kind, state) VALUES ('scan', 'stalled')")


def test_a_job_starts_queued_with_no_claim(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO jobs (kind) VALUES ('scan')")
    row = conn.execute("SELECT * FROM jobs").fetchone()
    assert row["state"] == "queued"
    assert row["lane"] == "normal"
    assert row["attempts"] == 0
    assert row["claim_token"] is None
    assert row["created_at"] is not None


def test_the_tokens_table_accepts_only_the_two_scopes(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO api_tokens (name, token_hash, scope) VALUES ('a', 'x', 'read')")
    conn.execute("INSERT INTO api_tokens (name, token_hash, scope) VALUES ('b', 'y', 'read_write')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO api_tokens (name, token_hash, scope) VALUES ('c', 'z', 'admin')")


def test_a_failed_migration_leaves_the_database_unchanged(tmp_path: Path) -> None:
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "0001_good.sql").write_text(
        "CREATE TABLE first_table (id INTEGER PRIMARY KEY);\n", encoding="utf-8"
    )
    (folder / "0002_broken.sql").write_text(
        "CREATE TABLE second_table (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE oops (this is not sql;\n",
        encoding="utf-8",
    )
    conn = connect(tmp_path / "broken.db")
    try:
        with pytest.raises(sqlite3.Error):
            migrate(conn, folder)
        assert applied_versions(conn) == {1}
        names = table_names(conn)
        assert "first_table" in names
        assert "second_table" not in names
        assert "oops" not in names
    finally:
        conn.close()


def test_a_migration_file_with_a_bad_name_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "jobs.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(ValueError):
        discover(folder)


def test_two_migrations_with_the_same_number_are_refused(tmp_path: Path) -> None:
    folder = tmp_path / "migrations"
    folder.mkdir()
    (folder / "0001_one.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (folder / "0001_two.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(ValueError):
        discover(folder)


def test_split_statements_keeps_a_semicolon_inside_a_string() -> None:
    statements = split_statements("INSERT INTO t VALUES ('a;b');\nSELECT 1;\n")
    assert len(statements) == 2
    assert "a;b" in statements[0]


def test_every_connection_uses_wal_and_foreign_keys(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_foreign_keys_are_enforced(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        conn.execute(
            "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL "
            "REFERENCES parent (id))"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO child (id, parent_id) VALUES (1, 99)")
    finally:
        conn.close()
