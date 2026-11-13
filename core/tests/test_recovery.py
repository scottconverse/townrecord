import json

import pytest

from townrecord import artifacts
from townrecord.config import Settings
from townrecord.db import connect, migrate
from townrecord.recovery import backup, restore
from townrecord.serving import ServiceLock


def test_private_profile_restores_and_rejects_corruption_or_existing_destination(tmp_path):
    profile = tmp_path / "original"
    profile.mkdir()
    settings = Settings(
        db_path=profile / "townrecord.db",
        storage_root=profile / "storage",
        runtime_root=profile,
        time_zone="America/Denver",
    )
    conn = connect(settings.db_path)
    migrate(conn)
    original_id = conn.execute("SELECT value FROM instance_identity").fetchone()[0]
    artifact = artifacts.store(conn, settings.storage_root, "record", b"Private agenda", "txt")
    conn.close()
    lock = ServiceLock.acquire(profile)
    try:
        with pytest.raises(ValueError, match="Stop TownRecord"):
            backup(settings, tmp_path / "refused")
    finally:
        lock.release()
    destination = tmp_path / "backup"
    manifest = backup(settings, destination)
    assert manifest["private"] and manifest["settings"]["time_zone"] == "America/Denver"
    restored = tmp_path / "restored"
    restore(destination, restored)
    fresh = connect(restored / "townrecord.db")
    try:
        assert fresh.execute("SELECT value FROM instance_identity").fetchone()[0] == original_id
        assert fresh.execute("SELECT sha256 FROM artifacts").fetchone()[0] == artifact.sha256
    finally:
        fresh.close()
    assert (restored / "storage" / artifact.rel_path).read_bytes() == b"Private agenda"
    with pytest.raises(ValueError, match="new directory"):
        restore(destination, restored)
    (destination / "storage" / artifact.rel_path).write_bytes(b"Damaged")
    with pytest.raises(ValueError, match="hash verification"):
        restore(destination, tmp_path / "damaged")
    assert not (tmp_path / "damaged").exists()
    manifest["files"]["storage/../../outside"] = artifact.sha256
    (destination / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        restore(destination, tmp_path / "unsafe")
