"""The pointer file that names the active runtime and the one before it (spec 8.9).

One small JSON file under the tool's folder:

.. code-block:: json

    {
      "version": 1,
      "active": {"version": "2026.08.19", "venv": "runtimes/yt-dlp/2026.8.19"},
      "previous": null
    }

``version`` is what the venv said when asked, which is the honest answer (spec
8.9 asks the venv rather than trusting the request), and ``venv`` is the folder
that holds it, written from the app-data root with forward slashes, so the
pointer never names a path outside the root the user chose.

The write follows the order the artifact store uses for the same reason (spec
8.6): write a temporary file in the same folder, flush and ``fsync`` it, then
``os.replace`` it over the pointer, which is atomic. A crash anywhere before
the replace leaves the old pointer exactly as it was, and the temporary file is
removed. Nothing here ever writes the pointer in place, because half a pointer
would leave TownRecord with no idea which yt-dlp is installed.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .settings import POINTER_NAME

#: The shape of the file. A later change to it is a new number.
POINTER_VERSION = 1


class PointerUnreadable(RuntimeError):
    """The pointer file is there but cannot be read as a pointer."""


@dataclass(frozen=True)
class Installed:
    """One installed runtime: the version its venv reported, and where it is."""

    version: str
    venv: str


@dataclass(frozen=True)
class Pointer:
    """Which runtime is active, and which one rollback would return to."""

    active: Installed | None = None
    previous: Installed | None = None

    def swapped(self) -> Pointer:
        """Return the pointer that rollback writes: the two swap places."""
        return Pointer(active=self.previous, previous=self.active)

    def as_json(self) -> dict[str, Any]:
        """Return the file's contents as plain JSON data."""
        return {
            "version": POINTER_VERSION,
            "active": None if self.active is None else _installed_json(self.active),
            "previous": None if self.previous is None else _installed_json(self.previous),
        }


def _installed_json(installed: Installed) -> dict[str, str]:
    return {"version": installed.version, "venv": installed.venv}


def pointer_path(tool_root: str | Path) -> Path:
    """Return the pointer file of a tool folder."""
    return Path(tool_root) / POINTER_NAME


def _installed_from(value: Any, name: str) -> Installed | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise PointerUnreadable(f"The pointer's {name} entry is not an object.")
    version = value.get("version")
    venv = value.get("venv")
    if not isinstance(version, str) or not version.strip():
        raise PointerUnreadable(f"The pointer's {name} entry names no version.")
    if not isinstance(venv, str) or not venv.strip():
        raise PointerUnreadable(f"The pointer's {name} entry names no runtime folder.")
    return Installed(version=version.strip(), venv=venv.strip())


def read_pointer(tool_root: str | Path) -> Pointer:
    """Read the pointer. A file that is not there yet reads as an empty pointer.

    The first run has nothing installed, which is not a failure: it is the
    state setup finds the machine in.
    """
    path = pointer_path(tool_root)
    if not path.is_file():
        return Pointer()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PointerUnreadable(f"The pointer file {path} could not be read: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise PointerUnreadable(f"The pointer file {path} is not readable JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PointerUnreadable(f"The pointer file {path} is not a JSON object.")
    return Pointer(
        active=_installed_from(data.get("active"), "active"),
        previous=_installed_from(data.get("previous"), "previous"),
    )


def _remove_quietly(path: Path) -> None:
    with contextlib.suppress(OSError):  # the temporary file may be gone already
        path.unlink()


def write_pointer(tool_root: str | Path, pointer: Pointer) -> None:
    """Write the pointer atomically: temporary file, fsync, then replace.

    Raises OSError when the write cannot be finished. The pointer file that is
    already on disk is untouched in that case, and the temporary file is
    removed, so the caller can record a reason and try again later.
    """
    folder = Path(tool_root)
    folder.mkdir(parents=True, exist_ok=True)
    path = pointer_path(folder)
    temporary = folder / f".{POINTER_NAME}.{uuid.uuid4().hex}.tmp"
    text = json.dumps(pointer.as_json(), indent=2, sort_keys=True) + "\n"
    try:
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        _remove_quietly(temporary)
        raise
