"""The content-addressed artifact store (spec 8.6, 10.5)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from townrecord.artifacts import (
    ArtifactIntegrityError,
    get,
    missing_sidecars,
    reconcile,
    record_missing_sidecar,
    store,
    verify,
)

#: The real caption capture, read in place (evidence/fixtures/README.md).
SRV3_FIXTURE = Path(
    r"C:\Users\scott\Desktop\Code\townrecord-oversight\evidence\fixtures\youtube\captions"
    r"\3qfQAkAAC9U.en.srv3"
)

#: Size and hash prefix from the fixture README table.
SRV3_SIZE = 1_429_554
SRV3_HASH_PREFIX = "b3199ecde3fe0244"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    """A storage root the user could have chosen."""
    root = tmp_path / "storage"
    root.mkdir()
    return root


def count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_store_writes_the_named_file_and_one_row(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    data = b"<transcript>hello</transcript>"
    artifact = store(conn, storage_root, "transcript", data, "srv3", {"video_id": "abc"})

    assert artifact.kind == "transcript"
    assert artifact.sha256 == sha256_bytes(data)
    assert artifact.rel_path == f"transcript-{artifact.sha256}.srv3"
    assert artifact.size == len(data)
    assert artifact.meta == {"video_id": "abc"}
    assert artifact.path.read_bytes() == data
    assert count(conn, "artifacts") == 1


def test_the_stored_path_is_relative_and_inside_the_root(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    artifact = store(conn, storage_root, "info", b"{}", "json", None)
    assert not Path(artifact.rel_path).is_absolute()
    assert ".." not in Path(artifact.rel_path).parts
    assert artifact.rel_path.startswith("info-")
    assert artifact.path == storage_root / artifact.rel_path


def test_the_meta_column_holds_json(conn: sqlite3.Connection, storage_root: Path) -> None:
    artifact = store(
        conn, storage_root, "transcript", b"x", "vtt", {"origin": "publisher captions"}
    )
    row = conn.execute("SELECT meta FROM artifacts WHERE id = ?", (artifact.id,)).fetchone()
    assert json.loads(row["meta"]) == {"origin": "publisher captions"}


def test_same_bytes_twice_is_one_file_and_one_row(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    data = b"the same meeting, twice"
    first = store(conn, storage_root, "transcript", data, "vtt", {"video_id": "abc"})
    second = store(conn, storage_root, "transcript", data, "vtt", {"video_id": "abc"})

    assert first.id == second.id
    assert count(conn, "artifacts") == 1
    assert len(list(storage_root.iterdir())) == 1
    assert first.path.read_bytes() == data


def test_a_row_whose_file_disappeared_is_written_again(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    data = b"the bytes that are still described by a row"
    first = store(conn, storage_root, "transcript", data, "vtt", None)
    first.path.unlink()

    second = store(conn, storage_root, "transcript", data, "vtt", None)
    assert second.id == first.id
    assert count(conn, "artifacts") == 1
    assert second.path.read_bytes() == data
    assert verify(conn, second.id, storage_root) is True


def test_a_different_file_at_the_same_name_is_an_error_and_untouched(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    data = b"the real bytes"
    artifact = store(conn, storage_root, "transcript", data, "vtt", None)

    # Someone changed the file after it was stored. The name still claims the
    # original hash.
    artifact.path.write_bytes(b"tampered bytes")
    before = sha256_file(artifact.path)

    with pytest.raises(ArtifactIntegrityError) as caught:
        store(conn, storage_root, "transcript", data, "vtt", None)

    assert artifact.sha256 in str(caught.value)
    assert sha256_file(artifact.path) == before
    assert artifact.path.read_bytes() == b"tampered bytes"
    assert count(conn, "artifacts") == 1


def test_a_crash_before_the_rename_leaves_no_file_and_no_row(
    conn: sqlite3.Connection, storage_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = b"interrupted capture"

    def boom(temp: Path, final: Path) -> None:
        raise OSError("simulated crash between write and rename")

    monkeypatch.setattr("townrecord.artifacts._publish", boom)
    with pytest.raises(OSError):
        store(conn, storage_root, "transcript", data, "srv3", {"video_id": "abc"})

    assert list(storage_root.iterdir()) == []
    assert count(conn, "artifacts") == 0


def test_a_temp_file_that_does_not_match_is_refused(
    conn: sqlite3.Connection, storage_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hash check after the write catches a short write."""
    real_write = os.write

    def short_write(fd: int, payload: bytes) -> int:
        return real_write(fd, payload[:-2] if len(payload) > 2 else payload)

    monkeypatch.setattr("townrecord.artifacts._write_bytes", short_write)
    with pytest.raises(ArtifactIntegrityError):
        store(conn, storage_root, "transcript", b"a long enough payload", "vtt", None)

    assert list(storage_root.iterdir()) == []
    assert count(conn, "artifacts") == 0


def test_a_kind_or_extension_that_could_leave_the_root_is_refused(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    for kind, ext in (("../escape", "vtt"), ("transcript", "../x"), ("a/b", "vtt")):
        with pytest.raises(ValueError):
            store(conn, storage_root, kind, b"x", ext, None)
    assert count(conn, "artifacts") == 0


def test_a_relative_storage_root_is_refused(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        store(conn, Path("storage"), "transcript", b"x", "vtt", None)


def test_verify_accepts_an_intact_artifact_and_catches_a_changed_one(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    artifact = store(conn, storage_root, "transcript", b"cited words", "vtt", None)
    assert verify(conn, artifact.id, storage_root) is True

    artifact.path.write_bytes(b"other words")
    assert verify(conn, artifact.id, storage_root) is False


def test_verify_is_false_for_a_file_that_disappeared(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    artifact = store(conn, storage_root, "transcript", b"cited words", "vtt", None)
    artifact.path.unlink()
    assert verify(conn, artifact.id, storage_root) is False


def test_verify_is_false_for_an_unknown_artifact(conn: sqlite3.Connection, tmp_path: Path) -> None:
    assert verify(conn, 4242, tmp_path) is False


def test_reconcile_finds_nothing_in_a_healthy_store(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    store(conn, storage_root, "transcript", b"one", "srv3", None)
    store(conn, storage_root, "info", b"two", "json", None)

    report = reconcile(conn, storage_root)
    assert report.ok is True
    assert report.findings == ()


def test_reconcile_reports_a_row_with_no_file(conn: sqlite3.Connection, storage_root: Path) -> None:
    artifact = store(conn, storage_root, "transcript", b"one", "srv3", None)
    artifact.path.unlink()

    report = reconcile(conn, storage_root)
    assert report.ok is False
    assert report.missing_files == (
        f"The file for artifact {artifact.id} "
        f"({artifact.rel_path}) is missing from the storage root.",
    )
    assert artifact.rel_path in report.findings[0]


def test_reconcile_reports_a_file_with_no_row(conn: sqlite3.Connection, storage_root: Path) -> None:
    store(conn, storage_root, "transcript", b"one", "srv3", None)
    orphan = storage_root / "transcript-deadbeef.vtt"
    orphan.write_bytes(b"nobody recorded me")

    report = reconcile(conn, storage_root)
    assert report.ok is False
    assert report.missing_files == ()
    assert report.changed_files == ()
    assert len(report.unrecorded_files) == 1
    assert "transcript-deadbeef.vtt" in report.unrecorded_files[0]
    assert "no row" in report.unrecorded_files[0]

    # Reconcile reports. It deletes nothing and fixes nothing.
    assert orphan.read_bytes() == b"nobody recorded me"
    assert count(conn, "artifacts") == 1


def test_reconcile_reports_a_file_that_no_longer_matches_its_name(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    artifact = store(conn, storage_root, "transcript", b"one", "srv3", None)
    artifact.path.write_bytes(b"changed after the row was written")

    report = reconcile(conn, storage_root)
    assert report.ok is False
    assert report.missing_files == ()
    assert report.unrecorded_files == ()
    assert len(report.changed_files) == 1
    assert artifact.rel_path in report.changed_files[0]
    assert artifact.sha256 in report.changed_files[0]

    # Reconcile reports. It does not repair the file.
    assert artifact.path.read_bytes() == b"changed after the row was written"


def test_reconcile_reports_a_named_file_with_no_row_that_changed(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    """A file that claims a hash it does not have is a change, not an orphan."""
    named = storage_root / f"transcript-{sha256_bytes(b'expected')}.vtt"
    named.write_bytes(b"not what the name says")

    report = reconcile(conn, storage_root)
    assert report.unrecorded_files == ()
    assert len(report.changed_files) == 1
    assert named.name in report.changed_files[0]


def test_reconcile_reports_every_row_when_the_root_is_gone(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    store(conn, storage_root, "transcript", b"one", "srv3", None)
    artifact_root = storage_root
    artifact_root.rename(storage_root.parent / "moved-away")

    report = reconcile(conn, storage_root)
    assert len(report.missing_files) == 1
    assert report.ok is False


def test_reconcile_ignores_a_temp_file_still_being_written(
    conn: sqlite3.Connection, storage_root: Path
) -> None:
    (storage_root / ".transcript-abc.vtt.1234.tmp").write_bytes(b"in progress")
    report = reconcile(conn, storage_root)
    assert report.ok is True


def test_record_missing_sidecar_keeps_the_reason(conn: sqlite3.Connection) -> None:
    record_missing_sidecar(conn, "3qfQAkAAC9U", "The info.json sidecar is not on the channel.")
    rows = missing_sidecars(conn)
    assert len(rows) == 1
    assert rows[0]["video_id"] == "3qfQAkAAC9U"
    assert rows[0]["reason"] == "The info.json sidecar is not on the channel."
    assert rows[0]["created_at"] is not None


def test_record_missing_sidecar_needs_a_reason(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        record_missing_sidecar(conn, "3qfQAkAAC9U", "   ")
    with pytest.raises(ValueError):
        record_missing_sidecar(conn, "", "no video")
    assert count(conn, "missing_sidecars") == 0


def test_get_returns_the_stored_artifact(conn: sqlite3.Connection, storage_root: Path) -> None:
    artifact = store(conn, storage_root, "info", b"{}", "json", {"video_id": "abc"})
    again = get(conn, artifact.id, storage_root)
    assert again is not None
    assert again.sha256 == artifact.sha256
    assert again.meta == {"video_id": "abc"}
    assert get(conn, 9999, storage_root) is None


@pytest.mark.skipif(not SRV3_FIXTURE.exists(), reason=f"fixture not present: {SRV3_FIXTURE}")
def test_a_real_size_srv3_caption_is_stored(conn: sqlite3.Connection, storage_root: Path) -> None:
    data = SRV3_FIXTURE.read_bytes()
    assert len(data) == SRV3_SIZE

    artifact = store(
        conn, storage_root, "transcript", data, "srv3", {"origin": "publisher captions"}
    )
    assert artifact.sha256.startswith(SRV3_HASH_PREFIX)
    assert artifact.size == SRV3_SIZE
    assert artifact.path.stat().st_size == SRV3_SIZE
    assert verify(conn, artifact.id, storage_root) is True
    assert reconcile(conn, storage_root).ok is True
