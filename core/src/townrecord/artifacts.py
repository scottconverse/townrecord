"""The content-addressed artifact store (spec 8.6, 10.5).

An artifact is a file that never changes and whose name carries the SHA-256 of
its own bytes: ``transcript-{sha256}.srv3``, ``.vtt`` or ``.json``, and
``info-{sha256}.json``. Storing the same bytes twice is the same artifact, so
the store is idempotent: it returns the row that is already there and writes
nothing.

Spec 8.6 fixes the order of a write, and the order is the point:

1. write the bytes to a temporary file in the same folder,
2. flush and ``fsync`` it, so the bytes are on disk and not in the page cache,
3. hash the temporary file again, from disk, and compare it with the hash in
   the name,
4. rename it into place with ``os.replace`` (the same folder, so the rename is
   atomic),
5. hash the final file once more.

If a step fails, the temporary file is removed and no row is written. A crash
between the write and the rename leaves no final file and no row, so the next
startup sees nothing rather than half a capture.

Different bytes at an existing path are an error (``ArtifactIntegrityError``).
The store never overwrites an artifact.

The database is the source of truth (spec 8.7, decision 10 rule C), so nothing
here creates a table. The schema is migration ``0004_artifacts.sql``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The read size for hashing a file from disk.
CHUNK_SIZE = 1024 * 1024

#: The kinds the spec names (spec 8.6). The store accepts any plain name.
TRANSCRIPT = "transcript"
INFO = "info"

#: A kind is a lowercase word. It never carries a separator, so a caller
#: cannot steer the file out of the storage root.
KIND_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: An extension is a lowercase word without a dot, for the same reason.
EXT_PATTERN = re.compile(r"^[a-z0-9]{1,12}$")

#: The whole name, as spec 8.6 builds it.
NAME_PATTERN = re.compile(
    r"^(?P<kind>[a-z0-9][a-z0-9_-]*)-(?P<sha256>[0-9a-f]{64})\.(?P<ext>[a-z0-9]{1,12})$"
)

#: The prefix of a temporary file. It is skipped by `reconcile`.
TEMP_MARKER = "."


class ArtifactIntegrityError(RuntimeError):
    """The bytes on disk disagree with the hash of the artifact."""


@dataclass(frozen=True)
class Artifact:
    """One row of the `artifacts` table, with the root it was stored under."""

    id: int
    kind: str
    sha256: str
    rel_path: str
    size: int
    meta: dict[str, Any]
    created_at: str
    storage_root: Path

    @property
    def path(self) -> Path:
        """The full path of the file on this machine."""
        return self.storage_root / self.rel_path


@dataclass(frozen=True)
class ReconcileReport:
    """What a startup check found by comparing files on disk with rows.

    Each entry is one plain sentence for the user (spec 16.3). The report is
    only a report: it deletes nothing and fixes nothing.
    """

    missing_files: tuple[str, ...] = ()
    unrecorded_files: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()

    @property
    def findings(self) -> tuple[str, ...]:
        """Every sentence, in the order the spec lists the cases."""
        return self.missing_files + self.unrecorded_files + self.changed_files

    @property
    def ok(self) -> bool:
        """True when the files and the rows agree."""
        return not self.findings


def store(
    conn: sqlite3.Connection,
    storage_root: str | Path,
    kind: str,
    data: bytes,
    ext: str,
    meta: Mapping[str, Any] | None = None,
) -> Artifact:
    """Store bytes as a content-addressed artifact. Return the artifact row.

    The name is `{kind}-{sha256}.{ext}`. The same bytes stored twice return the
    same artifact and write nothing the second time. Different bytes at an
    existing name raise `ArtifactIntegrityError` and change nothing.
    """
    root = _checked_root(storage_root)
    _check_name_parts(kind, ext)
    payload = bytes(data)
    sha = hashlib.sha256(payload).hexdigest()
    rel_path = f"{kind}-{sha}.{ext}"
    final = root / rel_path

    known = _find(conn, kind, sha)
    if known is not None and known["rel_path"] != rel_path:
        raise ArtifactIntegrityError(
            f"These bytes are already stored as {known['rel_path']} (artifact {known['id']}), "
            f"not as {rel_path}. TownRecord never replaces an artifact."
        )

    if final.exists():
        if not final.is_file():
            raise ArtifactIntegrityError(
                f"{rel_path} is a folder, not a file, so TownRecord will not touch it."
            )
        on_disk = hash_file(final)
        if on_disk != sha:
            raise ArtifactIntegrityError(
                f"{rel_path} does not hold the bytes its name claims: the name says {sha}, "
                f"the file on disk hashes to {on_disk}. TownRecord never overwrites an artifact."
            )
        if known is not None:
            return _row_to_artifact(known, root)

    _write_verified(root, rel_path, final, payload, sha)
    if known is None:
        _insert(conn, kind, sha, rel_path, len(payload), _meta_text(meta))
    # A row whose file went missing is written again here, never re-recorded:
    # the bytes match the hash in the name, so the row still describes them.
    return _row_to_artifact(_find(conn, kind, sha), root)


def merge_meta(
    conn: sqlite3.Connection, artifact_id: int, meta: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Add facts to an artifact's metadata, and return the whole record.

    An artifact's metadata is written with its bytes, and the bytes never
    change (spec 8.6). This is for the facts that are only known after the
    bytes arrive: a local transcription stores the JSON, and the provenance of
    spec 8.5 is written with the transcript row it belongs to.

    The same facts written twice change nothing, which is what makes a job that
    ran twice the same artifact and the same transcript (spec 8.7). A *different*
    value for a key that is already stored raises instead of replacing it.

    Raises:
        ArtifactIntegrityError: There is no such artifact, or a key is already
            recorded with another value.
    """
    row = conn.execute("SELECT meta FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
    if row is None:
        raise ArtifactIntegrityError(f"There is no artifact with id {artifact_id}.")
    current = _meta_dict(row["meta"])
    added = dict(meta or {})
    for key, value in added.items():
        if key in current and current[key] != value:
            raise ArtifactIntegrityError(
                f"Artifact {artifact_id} already records {key} as {current[key]!r}, and "
                f"{value!r} would replace it. TownRecord never rewrites what an artifact says."
            )
    if added and any(current.get(key) != value for key, value in added.items()):
        current = {**current, **added}
        conn.execute(
            "UPDATE artifacts SET meta = ? WHERE id = ?", (_meta_text(current), artifact_id)
        )
    return current


def get(conn: sqlite3.Connection, artifact_id: int, storage_root: str | Path) -> Artifact | None:
    """Return the artifact row, or None when there is no such row.

    The storage root is not in the row (it is relative by design, so the user
    can move the root), so the caller passes the root it wants to read from.
    """
    row = conn.execute(
        "SELECT id, kind, sha256, rel_path, size, meta, created_at FROM artifacts WHERE id = ?",
        (artifact_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_artifact(row, Path(storage_root).expanduser())


def verify(conn: sqlite3.Connection, artifact_id: int, storage_root: str | Path) -> bool:
    """Recompute the hash of a stored artifact (spec 10.5).

    A citation names an artifact by its hash. Before anything is exported, the
    hash is checked again, so a citation to an artifact that changed or
    disappeared is caught. An unknown id is False: it cannot be verified.
    """
    row = conn.execute(
        "SELECT rel_path, sha256 FROM artifacts WHERE id = ?", (artifact_id,)
    ).fetchone()
    if row is None:
        return False
    path = Path(storage_root).expanduser() / str(row["rel_path"])
    if not path.is_file():
        return False
    return hash_file(path) == str(row["sha256"])


def record_missing_sidecar(conn: sqlite3.Connection, video_id: str, reason: str) -> int:
    """Record that a video has no info.json sidecar, and why (spec 8.6).

    A capture without its sidecar is never a silent success. The rows are
    append only, so a later check that changes its answer adds a row instead of
    rewriting the first one. Returns the id of the new row.
    """
    if not str(video_id).strip():
        raise ValueError("A missing sidecar needs the video id it belongs to.")
    if not str(reason).strip():
        raise ValueError("A missing sidecar is recorded with its reason, never without one.")
    cursor = conn.execute(
        "INSERT INTO missing_sidecars (video_id, reason) VALUES (?, ?)",
        (str(video_id).strip(), str(reason).strip()),
    )
    return int(cursor.lastrowid or 0)


def missing_sidecars(conn: sqlite3.Connection, video_id: str | None = None) -> list[sqlite3.Row]:
    """Return the recorded missing sidecars, newest last."""
    if video_id is None:
        return conn.execute(
            "SELECT id, video_id, reason, created_at FROM missing_sidecars ORDER BY id"
        ).fetchall()
    return conn.execute(
        "SELECT id, video_id, reason, created_at FROM missing_sidecars "
        "WHERE video_id = ? ORDER BY id",
        (video_id,),
    ).fetchall()


def reconcile(conn: sqlite3.Connection, storage_root: str | Path) -> ReconcileReport:
    """Compare the files on disk with the rows (spec 8.6, run at startup).

    Three cases, each a plain sentence:

    - a row whose file is missing,
    - a file in the storage root with no row,
    - a file whose bytes no longer match the hash in its name.

    This only reports. It deletes nothing and fixes nothing. Temporary files
    (names starting with a dot) are writes in progress and are not reported.
    """
    root = Path(storage_root).expanduser()
    rows = conn.execute("SELECT id, rel_path, sha256 FROM artifacts ORDER BY id").fetchall()
    recorded = {row["rel_path"] for row in rows}

    missing: list[str] = []
    changed: list[str] = []
    for row in rows:
        path = root / row["rel_path"]
        if not path.is_file():
            missing.append(
                f"The file for artifact {row['id']} ({row['rel_path']}) is missing "
                f"from the storage root."
            )
            continue
        on_disk = hash_file(path)
        if on_disk != row["sha256"]:
            changed.append(_changed_sentence(row["rel_path"], row["sha256"], on_disk))

    unrecorded: list[str] = []
    for path in _files_under(root):
        rel_path = path.relative_to(root).as_posix()
        if rel_path in recorded:
            continue
        claimed = _hash_in_name(path.name)
        if claimed is None:
            unrecorded.append(
                f"The file {rel_path} is in the storage root but has no row in the database."
            )
            continue
        on_disk = hash_file(path)
        if on_disk != claimed:
            changed.append(_changed_sentence(rel_path, claimed, on_disk))
        else:
            unrecorded.append(
                f"The file {rel_path} is in the storage root but has no row in the database."
            )

    return ReconcileReport(tuple(missing), tuple(sorted(unrecorded)), tuple(sorted(changed)))


def _write_verified(root: Path, rel_path: str, final: Path, payload: bytes, sha: str) -> None:
    """Write the bytes, check them on disk, rename into place, check again."""
    temp = root / f"{TEMP_MARKER}{rel_path}.{uuid.uuid4().hex}.tmp"
    try:
        _write_temp(temp, payload)
        written = hash_file(temp)
        if written != sha:
            raise ArtifactIntegrityError(
                f"The temporary file for {rel_path} does not hold the bytes that were written: "
                f"it hashes to {written}, not {sha}."
            )
        _publish(temp, final)
    except BaseException:
        _remove_quietly(temp)
        raise
    published = hash_file(final)
    if published != sha:
        raise ArtifactIntegrityError(
            f"{rel_path} does not hold the bytes its name claims after the rename: "
            f"the name says {sha}, the file hashes to {published}."
        )


def _write_temp(temp: Path, payload: bytes) -> None:
    """Write every byte, flush it and ask the operating system to put it on disk."""
    with open(temp, "wb", buffering=0) as handle:
        _write_bytes(handle.fileno(), payload)
        os.fsync(handle.fileno())


def _write_bytes(fd: int, payload: bytes) -> int:
    """Write the payload to an open file descriptor.

    One call, one count. A short write is not corrected here: the hash check
    after the write is what catches it, and tests replace this to model one.
    """
    return os.write(fd, payload)


def _publish(temp: Path, final: Path) -> None:
    """Rename the temporary file into place. Same folder, so this is atomic."""
    os.replace(temp, final)


def _insert(
    conn: sqlite3.Connection, kind: str, sha: str, rel_path: str, size: int, meta: str
) -> None:
    conn.execute(
        "INSERT INTO artifacts (kind, sha256, rel_path, size, meta) VALUES (?, ?, ?, ?, ?)",
        (kind, sha, rel_path, size, meta),
    )


def _find(conn: sqlite3.Connection, kind: str, sha: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, kind, sha256, rel_path, size, meta, created_at "
        "FROM artifacts WHERE kind = ? AND sha256 = ?",
        (kind, sha),
    ).fetchone()


def _row_to_artifact(row: sqlite3.Row, root: Path) -> Artifact:
    return Artifact(
        id=int(row["id"]),
        kind=str(row["kind"]),
        sha256=str(row["sha256"]),
        rel_path=str(row["rel_path"]),
        size=int(row["size"]),
        meta=_meta_dict(row["meta"]),
        created_at=str(row["created_at"]),
        storage_root=root,
    )


def _meta_text(meta: Mapping[str, Any] | None) -> str:
    if meta is None:
        return "{}"
    return json.dumps(dict(meta), sort_keys=True)


def _meta_dict(text: Any) -> dict[str, Any]:
    if not text:
        return {}
    try:
        loaded = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _checked_root(storage_root: str | Path) -> Path:
    """Return the storage root as an absolute path (spec 8.6)."""
    root = Path(storage_root).expanduser()
    if not root.is_absolute():
        raise ValueError("The storage root must be an absolute path.")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _check_name_parts(kind: str, ext: str) -> None:
    if not KIND_PATTERN.match(str(kind)):
        raise ValueError(
            f"An artifact kind is a lowercase word: {kind!r} is not one, so it is refused."
        )
    if not EXT_PATTERN.match(str(ext)):
        raise ValueError(
            f"An artifact extension is a lowercase word: {ext!r} is not one, so it is refused."
        )


def _hash_in_name(name: str) -> str | None:
    match = NAME_PATTERN.match(name)
    return None if match is None else match.group("sha256")


def _changed_sentence(rel_path: str, claimed: str, on_disk: str) -> str:
    return (
        f"The file {rel_path} does not match its name: the name says {claimed}, "
        f"the file hashes to {on_disk}."
    )


def _files_under(root: Path) -> Iterator[Path]:
    """Yield every real file under the root, leaving out writes in progress."""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part.startswith(TEMP_MARKER) for part in path.relative_to(root).parts):
            continue
        yield path


def hash_file(path: Path) -> str:
    """Hash a file from disk, in chunks, so a large capture fits in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _remove_quietly(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()
