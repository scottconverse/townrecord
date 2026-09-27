"""The pointer file that names the active runtime (spec 8.9, 8.6).

The write is the same shape the artifact store uses: a temporary file in the
same folder, flush, `fsync`, then `os.replace`, which is atomic. The tests here
are about the promise that shape makes, which is that a crash never leaves
TownRecord with a half-written idea of which yt-dlp is installed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from townrecord.runtime.pointer import (
    Pointer,
    PointerUnreadable,
    pointer_path,
    read_pointer,
    write_pointer,
)

from .fakes import VENV_SPELLING, installed, write_installed


@pytest.fixture
def tool_root(app_root: Path) -> Path:
    """The folder that holds every yt-dlp version, and the pointer."""
    return app_root / "runtimes" / "yt-dlp"


def test_a_pointer_round_trips(tool_root: Path) -> None:
    """Both entries come back the way they went in."""
    older = installed("2026.8.19", VENV_SPELLING)
    newer = installed("2026.9.1", "2026.09.01")
    write_pointer(tool_root, Pointer(active=newer, previous=older))
    assert read_pointer(tool_root) == Pointer(active=newer, previous=older)


def test_a_missing_pointer_reads_as_nothing_installed(tool_root: Path) -> None:
    """The first run has nothing installed, which is not a failure."""
    assert read_pointer(tool_root) == Pointer()


def test_the_pointer_holds_no_absolute_path(tool_root: Path) -> None:
    """The folder is written from the app-data root, so no machine leaks in."""
    write_pointer(tool_root, Pointer(active=installed("2026.8.19", VENV_SPELLING), previous=None))
    text = pointer_path(tool_root).read_text(encoding="utf-8")
    assert str(tool_root) not in text
    assert json.loads(text)["active"]["venv"] == "runtimes/yt-dlp/2026.8.19"


def test_a_crash_during_the_write_leaves_the_old_pointer_intact(
    app_root: Path, tool_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old pointer stands, and no temporary file is left lying about."""
    old = installed("2026.8.19", VENV_SPELLING)
    new = installed("2026.9.1", "2026.09.01")
    write_installed(app_root, old, None)
    before = pointer_path(tool_root).read_bytes()

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("the disk went away")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="the disk went away"):
        write_pointer(tool_root, Pointer(active=new, previous=old))

    assert pointer_path(tool_root).read_bytes() == before
    assert read_pointer(tool_root).active == old
    assert read_pointer(tool_root).previous is None
    assert [path.name for path in tool_root.iterdir()] == ["current.json"]


def test_a_pointer_that_is_not_json_is_a_plain_reason(tool_root: Path) -> None:
    """Half a pointer is a reason the user can read, not a crash."""
    tool_root.mkdir(parents=True, exist_ok=True)
    pointer_path(tool_root).write_text("{not json", encoding="utf-8")
    with pytest.raises(PointerUnreadable, match="not readable JSON"):
        read_pointer(tool_root)


def test_an_entry_without_a_version_is_a_plain_reason(tool_root: Path) -> None:
    """An entry that names no version cannot be trusted to name a runtime."""
    tool_root.mkdir(parents=True, exist_ok=True)
    pointer_path(tool_root).write_text(
        json.dumps(
            {"version": 1, "active": {"venv": "runtimes/yt-dlp/2026.8.19"}, "previous": None}
        ),
        encoding="utf-8",
    )
    with pytest.raises(PointerUnreadable, match="names no version"):
        read_pointer(tool_root)


def test_swapping_puts_the_previous_version_in_front(tool_root: Path) -> None:
    """What rollback writes: the two entries change places."""
    older = installed("2026.8.19", VENV_SPELLING)
    newer = installed("2026.9.1", "2026.09.01")
    assert Pointer(active=newer, previous=older).swapped() == Pointer(active=older, previous=newer)
