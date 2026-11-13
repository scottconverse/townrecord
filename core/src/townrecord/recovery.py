"""Portable stopped-service backups: SQLite plus its referenced immutable files."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from .config import Settings
from .serving import ServiceLock


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def child(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError("Backup contains a path outside its storage folder.")
    return path


def backup(settings: Settings, destination: Path) -> dict:
    """Never overwrite a backup. Incomplete directories lack a valid manifest."""
    if not settings.db_path.is_file():
        raise ValueError("No TownRecord database exists at the configured path.")
    target = destination.resolve()
    if target.exists():
        raise ValueError("Choose a new backup directory; existing backups are never overwritten.")
    if target.is_relative_to(settings.storage_root.resolve()):
        raise ValueError("Put the backup outside the active artifact storage folder.")
    lock = ServiceLock.acquire(settings.runtime_root)
    if not lock.held:
        raise ValueError("Stop TownRecord before making a backup.")
    try:
        target.mkdir(parents=True)
        with (
            closing(sqlite3.connect(settings.db_path)) as source,
            closing(sqlite3.connect(target / "townrecord.db")) as copy,
        ):
            source.backup(copy)
            rows = copy.execute("SELECT rel_path,sha256 FROM artifacts").fetchall()
        files = {"townrecord.db": digest(target / "townrecord.db")}
        for relative, expected in rows:
            original = child(settings.storage_root, relative)
            if not original.is_file() or digest(original) != expected:
                raise ValueError(f"Artifact is missing or changed: {relative}")
            output = child(target / "storage", relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, output)
            if digest(output) != expected:
                raise ValueError(f"Backup copy verification failed: {relative}")
            files["storage/" + relative.replace("\\", "/")] = expected
        manifest = {
            "format": "townrecord-backup-v1",
            "version": settings.version,
            "files": files,
            "settings": {
                "time_zone": settings.time_zone,
                "daily_time": settings.daily_time.strftime("%H:%M"),
                "test_video_url": settings.test_video_url,
            },
            "private": True,
        }
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest
    finally:
        lock.release()


def restore(source: Path, destination: Path) -> dict:
    """Validate everything first, then restore to a new, non-running profile."""
    source, target = source.resolve(), destination.resolve()
    if target.exists():
        raise ValueError(
            "Restore requires a new directory; existing city data is never overwritten."
        )
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "townrecord-backup-v1":
        raise ValueError("This is not a supported TownRecord backup.")
    files = manifest.get("files", {})
    if "townrecord.db" not in files:
        raise ValueError("The backup does not contain its database.")
    for relative, expected in files.items():
        if relative != "townrecord.db" and not relative.startswith("storage/"):
            raise ValueError("The backup contains an unsupported file path.")
        item = child(source, relative)
        if not item.is_file() or digest(item) != expected:
            raise ValueError(f"Backup hash verification failed: {relative}")
    with closing(
        sqlite3.connect(f"file:{(source / 'townrecord.db').as_posix()}?mode=ro", uri=True)
    ) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Backup database integrity check failed.")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Backup database relationships are invalid.")
        for relative, expected in db.execute("SELECT rel_path,sha256 FROM artifacts"):
            if files.get("storage/" + relative.replace("\\", "/")) != expected:
                raise ValueError("The manifest omits a database artifact.")
    target.mkdir(parents=True)
    for relative in files:
        output = child(target, relative)
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(child(source, relative), output)
        if digest(output) != files[relative]:
            raise ValueError(f"Restore copy verification failed: {relative}")
    (target / "restored-settings.json").write_text(
        json.dumps(manifest["settings"], indent=2), encoding="utf-8"
    )
    return manifest
