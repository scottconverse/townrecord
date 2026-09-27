"""The migration system (spec 14.3), the jobs table (spec 16.1) and artifacts (spec 8.6)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from townrecord.db import applied_versions, connect, discover, migrate, split_statements

#: The migrations this worktree ships, in order. Both lanes are merged here:
#: 0006 holds a job back until a moment (spec 8.2), 0007 is the search index
#: (spec 12.4), 0008 is the window index that finds a phrase split by a line
#: break (spec 12.4), 0009 is the portal sync columns (spec 7.2, 9.2) and 0010
#: is the OCR reason of a page with no text layer (spec 9.6), 0012 is the
#: schedule's record of one run per task, subject and local day (spec 16.2) and
#: 0013 is the classifier settings of one source (spec 7.2 step 6), which are
#: per source rather than one city's seeds for every city (rule D).
#: 0011 is not in this worktree: it belongs to another unit, which is why the
#: numbers here are not contiguous.
SHIPPED_VERSIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13]


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def test_the_shipped_migrations_are_numbered_in_order() -> None:
    found = discover()
    assert [version for version, _, _ in found] == sorted(version for version, _, _ in found)
    assert [version for version, _, _ in found] == SHIPPED_VERSIONS


def test_migrate_applies_every_migration(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        applied = migrate(conn)
        assert applied == SHIPPED_VERSIONS
        assert applied_versions(conn) == set(SHIPPED_VERSIONS)
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


def test_the_jobs_table_can_hold_a_job_back_until_a_moment(conn: sqlite3.Connection) -> None:
    """Migration 0006: a job that cannot run yet says when to try again (spec 8.2)."""
    assert "run_after" in column_names(conn, "jobs")
    assert conn.execute("SELECT run_after FROM jobs").fetchall() == []
    conn.execute(
        "INSERT INTO jobs (kind, payload, lane, state) VALUES ('x', '1', 'normal', 'queued')"
    )
    row = conn.execute("SELECT run_after FROM jobs").fetchone()
    assert row["run_after"] is None, "a new job is claimable now"


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


def test_the_artifacts_table_has_the_columns_of_spec_8_6(conn: sqlite3.Connection) -> None:
    columns = column_names(conn, "artifacts")
    for expected in ("kind", "sha256", "rel_path", "size", "meta", "created_at"):
        assert expected in columns, f"artifacts is missing {expected}"


def test_one_kind_and_hash_pair_is_stored_once(conn: sqlite3.Connection) -> None:
    sha = "a" * 64
    conn.execute(
        "INSERT INTO artifacts (kind, sha256, rel_path, size) VALUES ('transcript', ?, ?, 5)",
        (sha, f"transcript-{sha}.vtt"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO artifacts (kind, sha256, rel_path, size) VALUES ('transcript', ?, ?, 5)",
            (sha, f"transcript-{sha}.vtt"),
        )
    # The same hash under another kind is a different artifact.
    conn.execute(
        "INSERT INTO artifacts (kind, sha256, rel_path, size) VALUES ('info', ?, ?, 5)",
        (sha, f"info-{sha}.json"),
    )


def test_an_absolute_artifact_path_is_refused(conn: sqlite3.Connection) -> None:
    insert = "INSERT INTO artifacts (kind, sha256, rel_path, size) VALUES ('transcript', ?, ?, 5)"
    for rel_path in ("/home/user/storage/transcript-abc.vtt", "C:/storage/transcript-abc.vtt"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("b" * 64, rel_path))


def test_a_missing_sidecar_needs_a_video_and_a_reason(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO missing_sidecars (video_id, reason) VALUES ('abc', 'not on the channel')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO missing_sidecars (video_id, reason) VALUES ('abc', '')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO missing_sidecars (video_id, reason) VALUES ('', 'why')")


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
