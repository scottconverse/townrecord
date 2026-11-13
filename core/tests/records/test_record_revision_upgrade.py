"""An existing populated database keeps its references through migration 20."""

import shutil

from townrecord.db import connect, discover, migrate
from townrecord.repo import get_meeting, get_record, insert_record_page, record_pages

from .conftest import Area, FakePortal, Sync
from .test_download_record import a_stored_meeting


def test_upgrade_retains_record_pages_and_meeting_identity(tmp_path, monkeypatch):
    old = tmp_path / "migrations"
    old.mkdir()
    for version, _, path in discover():
        if version < 20:
            shutil.copyfile(path, old / path.name)
    db = tmp_path / "existing.db"
    conn = connect(db)
    try:
        migrate(conn, old)
        area = Area(conn)
        portal = FakePortal()
        monkeypatch.setattr("townrecord.records.portal.open_client", portal.client)
        monkeypatch.setenv("TOWNRECORD_STORAGE", str(tmp_path / "storage"))
        sync = Sync(db, conn)
        mid = a_stored_meeting(sync, portal, area)
        # Seed the old schema directly; the new downloader needs migration 20.
        from townrecord.artifacts import store
        from townrecord.repo import insert_record

        item = store(conn, tmp_path / "storage", "record", b"original", "txt")
        rid = insert_record(
            conn,
            meeting_id=mid,
            kind="agenda",
            artifact_id=item.id,
            portal_document_id=18613,
            portal_template_id=16805,
        )
        insert_record_page(conn, record_id=rid, page_number=1, text="Original cited page")
        before = get_record(conn, rid)
        assert migrate(conn) == [20, 21, 22]
        assert get_record(conn, rid) == before
        assert get_meeting(conn, mid).portal_meeting_id == 4100
        assert record_pages(conn, rid)[0].text == "Original cited page"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert migrate(conn) == []
    finally:
        conn.close()


def test_rebuild_failure_rolls_back_and_restores_foreign_keys(tmp_path):
    import sqlite3

    import pytest

    db = connect(tmp_path / "rollback.db")
    folder = tmp_path / "broken-migration"
    folder.mkdir()
    (folder / "0001_broken.sql").write_text(
        "-- rebuild-tables\nCREATE TABLE parent(id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE child(id INTEGER REFERENCES parent(id));\n"
        "INSERT INTO child VALUES(99);\n"
    )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="invalid foreign key"):
            migrate(db, folder)
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='parent'").fetchone() is None
        assert db.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 0
    finally:
        db.close()
