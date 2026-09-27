"""Storage root validation (spec 8.6).

The user chooses where captured files live. The folder must be an absolute
path, outside the application folder, and writable. Every failure returns a
plain reason the interface can show, never an exception trace.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from pathlib import Path
from typing import NamedTuple

#: Name of the throwaway file used to prove the folder is writable.
PROBE_PREFIX = ".townrecord-write-test-"


class StorageRootCheck(NamedTuple):
    """The result of checking a folder the user chose."""

    ok: bool
    path: Path | None
    reason: str | None


def application_folder() -> Path:
    """Return the folder that holds the application code.

    `src/townrecord/storage.py` sits inside it, so the folder is two levels up.
    """
    return Path(__file__).resolve().parents[2]


def is_inside(child: Path, parent: Path) -> bool:
    """Return True when `child` is `parent` or a folder below it."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _plain(exc: OSError) -> str:
    """Turn an operating system error into one plain sentence."""
    text = (exc.strerror or str(exc)).strip().rstrip(".")
    return f"{text}." if text else "the operating system refused it."


def probe_write(folder: Path) -> str | None:
    """Write, flush and delete a test file. Return None on success, else a reason."""
    probe = folder / f"{PROBE_PREFIX}{uuid.uuid4().hex}"
    try:
        with open(probe, "wb") as handle:
            handle.write(b"townrecord storage check\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        _remove_quietly(probe)
        return _plain(exc)
    try:
        probe.unlink()
    except OSError as exc:
        return _plain(exc)
    return None


def _remove_quietly(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def check_storage_root(
    path: str | Path | None,
    *,
    app_folder: str | Path | None = None,
) -> StorageRootCheck:
    """Check a candidate storage root. Never raises for a bad folder."""
    if path is None or not str(path).strip():
        return StorageRootCheck(False, None, "No folder was given.")
    try:
        candidate = Path(path).expanduser()
    except (TypeError, ValueError):
        return StorageRootCheck(False, None, "That is not a usable folder path.")
    if not candidate.is_absolute():
        return StorageRootCheck(False, candidate, "The folder must be an absolute path.")

    chosen = candidate.resolve()
    application = application_folder() if app_folder is None else Path(app_folder).resolve()
    if is_inside(chosen, application):
        return StorageRootCheck(False, chosen, "The folder is inside the application folder.")

    if chosen.exists() and not chosen.is_dir():
        return StorageRootCheck(False, chosen, "That path is a file, not a folder.")
    if not chosen.exists():
        try:
            chosen.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return StorageRootCheck(
                False, chosen, f"The folder could not be created: {_plain(exc)}"
            )
        if not chosen.is_dir():
            return StorageRootCheck(False, chosen, "That path is a file, not a folder.")

    problem = probe_write(chosen)
    if problem is not None:
        return StorageRootCheck(False, chosen, f"The folder is not writable: {problem}")
    return StorageRootCheck(True, chosen, None)
