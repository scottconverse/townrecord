"""TR02/TR04: preserve the real pre-connector schema and its durable links.

These tests intentionally start at shipped migration 0018, not at the current
schema, so a fresh-database-only implementation cannot satisfy upgrade coverage.
All records and credentials are synthetic. No provider or model is contacted.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from townrecord.artifacts import store
from townrecord.db import applied_versions, connect, discover, migrate
from townrecord.jobs import enqueue
from townrecord.repo import (
    insert_body,
    insert_jurisdiction,
    insert_record,
    insert_record_citation,
    insert_record_page,
    insert_source,
    upsert_meeting,
)

LEGACY_VERSIONS = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 18}
LEGACY_TABLES = (
    "sources",
    "meetings",
    "records",
    "record_pages",
    "artifacts",
    "citations",
    "jobs",
)
CONNECTOR_TABLES = {
    "connector_state",
    "connector_state_history",
    "connector_captures",
    "connector_links",
}


def _legacy_database(tmp_path: Path) -> sqlite3.Connection:
    """Apply only the exact numbered old migrations, even after 0019 ships."""
    folder = tmp_path / "old"
    folder.mkdir()
    for version, _name, path in discover():
        if version in LEGACY_VERSIONS:
            shutil.copy2(path, folder / path.name)
    connection = connect(tmp_path / "old.db")
    assert set(migrate(connection, folder)) == LEGACY_VERSIONS
    return connection


def _seed_legacy_rows(conn: sqlite3.Connection, root: Path) -> dict[str, int]:
    city = insert_jurisdiction(conn, type="city", name="Synthetic city")
    body = insert_body(conn, jurisdiction_id=city, name="Council")
    source = insert_source(
        conn,
        jurisdiction_id=city,
        body_id=body,
        type="meeting_portal",
        origin="https://legacy.test.invalid",
        suggested_by="migration-test",
        reason="Synthetic legacy data",
        status="accepted",
    )
    meeting, created = upsert_meeting(
        conn,
        body_id=body,
        starts_at="2026-09-08T19:00:00",
        title="Council regular session",
        portal_source_id=source,
        portal_meeting_id=4100,
        portal_html_template_id=16805,
    )
    assert created
    artifact = store(
        conn,
        root,
        "record",
        b"Synthetic flood mitigation evidence.",
        "txt",
        meta={"citation_url": "https://legacy.test.invalid/Portal/Meeting?meetingTemplateId=16805"},
    )
    record = insert_record(
        conn,
        meeting_id=meeting,
        source_id=source,
        kind="agenda",
        artifact_id=artifact.id,
        portal_document_id=18613,
        portal_template_id=16805,
    )
    insert_record_page(conn, record_id=record, page_number=1, text="flood mitigation evidence")
    insert_record_citation(
        conn,
        record_id=record,
        source_id=source,
        artifact_id=artifact.id,
        excerpt="flood mitigation evidence",
        page_number=1,
    )
    payload = {
        "source_id": source,
        "meeting_id": meeting,
        "document_id": 18658,
        "template_id": 16823,
        "compile_output_type": 1,
        "template_name": "Agenda Packet",
    }
    pending = enqueue(conn, "download_record", payload)
    conn.execute(
        "UPDATE jobs SET checkpoint = ? WHERE id = ?",
        (json.dumps({"legacy_marker": "do-not-rewrite", "offset": 17}), pending),
    )
    return {"meeting": meeting, "record": record, "source": source, "job": pending}


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
        for table in LEGACY_TABLES
    }


def test_legacy_fixture_persists_integer_ids_queued_work_and_search(tmp_path: Path) -> None:
    conn = _legacy_database(tmp_path)
    try:
        ids = _seed_legacy_rows(conn, tmp_path / "storage")
        before = _snapshot(conn)
    finally:
        conn.close()
    conn = connect(tmp_path / "old.db")
    try:
        assert applied_versions(conn) == LEGACY_VERSIONS
        assert _snapshot(conn) == before
        assert conn.execute(
            "SELECT portal_document_id, portal_template_id FROM records WHERE id=?",
            (ids["record"],),
        ).fetchone()[:] == (18613, 16805)
        pending = conn.execute(
            "SELECT state, checkpoint, payload FROM jobs WHERE id=?", (ids["job"],)
        ).fetchone()
        assert pending["state"] == "queued"
        assert json.loads(pending["checkpoint"]) == {
            "legacy_marker": "do-not-rewrite",
            "offset": 17,
        }
        assert json.loads(pending["payload"])["document_id"] == 18658
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM record_page_search WHERE record_page_search MATCH ?",
                ("mitigation",),
            ).fetchone()[0]
            == 1
        )
        assert conn.execute("SELECT record_id, source_id FROM citations").fetchone()[:] == (
            ids["record"],
            ids["source"],
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_connector_migration_preserves_old_records_jobs_citations_and_fts(tmp_path: Path) -> None:
    conn = _legacy_database(tmp_path)
    try:
        ids = _seed_legacy_rows(conn, tmp_path / "storage")
        before = _snapshot(conn)
        schema_before = {
            row["name"]: row["sql"]
            for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
            if row["name"] in LEGACY_TABLES
        }
        assert migrate(conn) == [19], "TR04 requires an additive upgrade from the old database"
        assert _snapshot(conn) == before
        assert all(
            conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
            == definition
            for name, definition in schema_before.items()
        ), "existing tables must not be rebuilt or have their identity columns rewritten"
        assert conn.execute(
            "SELECT portal_meeting_id, portal_html_template_id FROM meetings WHERE id=?",
            (ids["meeting"],),
        ).fetchone()[:] == (4100, 16805)
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM record_page_search WHERE record_page_search MATCH ?",
                ("mitigation",),
            ).fetchone()[0]
            == 1
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert migrate(conn) == []
        assert _snapshot(conn) == before
        assert applied_versions(conn) == LEGACY_VERSIONS | {19}
    finally:
        conn.close()


def test_connector_state_capture_history_and_identity_mapping_are_application_owned(
    tmp_path: Path,
) -> None:
    conn = connect(tmp_path / "fresh.db")
    try:
        migrate(conn)
        names = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert names >= CONNECTOR_TABLES, (
            "TR01/TR02 and S11 require app-owned durable CAS state, capture bytes, "
            "revision history and string-identity linkage"
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
