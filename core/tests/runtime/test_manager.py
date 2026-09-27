"""The interpreter the capture job is handed, rollback, and the update lock.

Spec 8.9 asks for three things this file pins down: `interpreter()` names the
active venv or says plainly that there is none, it never falls back to the
system Python, and only one update runs at a time.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from townrecord.runtime.lock import LOCK_NAME, UpdateInProgress, UpdateLock
from townrecord.runtime.manager import (
    RollbackUnavailable,
    RuntimeManager,
    RuntimeNotInstalled,
)
from townrecord.runtime.pointer import Installed, Pointer, read_pointer, write_pointer
from townrecord.runtime.settings import RuntimeSettings

from .conftest import TEST_VIDEO_URL
from .fakes import (
    VENV_SPELLING,
    FakeProbe,
    FakeUV,
    installed,
    make_venv,
    manager_for,
    pypi_client,
    venv_python,
    write_installed,
)


@pytest.fixture
def tool_root(app_root: Path) -> Path:
    """The folder that holds every yt-dlp version, and the pointer."""
    return app_root / "runtimes" / "yt-dlp"


# -- the interpreter the capture job is handed -----------------------------


def test_interpreter_never_returns_the_system_python(app_root: Path) -> None:
    """With nothing installed it says so; with a runtime it names that venv."""
    manager = manager_for(app_root, FakeUV())
    with pytest.raises(RuntimeNotInstalled) as caught:
        manager.interpreter()
    assert "yt-dlp is not installed yet; run setup" in str(caught.value)
    assert sys.executable not in str(caught.value)

    result = manager.install("2026.8.19")
    write_installed(app_root, Installed(version=result.version, venv=result.venv), None)
    assert manager.interpreter() == result.python
    assert manager.interpreter() != sys.executable
    assert Path(manager.interpreter()).is_file()


def test_interpreter_is_never_the_interpreter_that_runs_the_service(app_root: Path) -> None:
    """Whatever else it answers, it is never the Python this service runs under.

    Either it names the active venv, or it says there is none. A silent fallback
    to the system Python is the failure the whole runtime answers: the
    allow-listed child environment cannot see a user-site yt-dlp, so the capture
    run dies with `No module named yt_dlp`.
    """
    manager = manager_for(app_root, FakeUV())
    try:
        handed = manager.interpreter()
    except RuntimeNotInstalled:
        handed = ""
    assert handed != sys.executable

    result = manager.install("2026.8.19")
    write_installed(app_root, Installed(version=result.version, venv=result.venv), None)
    handed = manager.interpreter()
    assert handed == result.python
    assert handed != sys.executable
    assert str(app_root) in handed  # it lives under the root the user chose


def test_an_interpreter_that_has_gone_is_a_plain_error_and_not_a_fallback(app_root: Path) -> None:
    """A deleted venv is a reason to run setup, never a reason to use the system Python."""
    manager = manager_for(app_root, FakeUV())
    result = manager.install("2026.8.19")
    write_installed(app_root, Installed(version=result.version, venv=result.venv), None)
    Path(result.python).unlink()

    with pytest.raises(RuntimeNotInstalled) as caught:
        manager.interpreter()
    assert "is not where the pointer says it is" in str(caught.value)
    assert "run setup" in str(caught.value)


def test_a_pointer_that_climbs_out_of_the_root_is_not_used(
    app_root: Path, tool_root: Path, tmp_path: Path
) -> None:
    """A folder path is resolved before it is checked, so `..` cannot climb out."""
    # The same folder reached two ways: written so the check reads it as inside
    # the root, but resolving to a folder that is outside it. The venv really is
    # out there, so only the check stands between the pointer and the system.
    escaped = venv_python(app_root / "runtimes" / "yt-dlp" / "../../../elsewhere")
    escaped.parent.mkdir(parents=True, exist_ok=True)
    escaped.write_text("#!/fake python\n", encoding="utf-8")
    assert escaped.is_file()
    assert escaped.resolve() == venv_python(tmp_path / "elsewhere").resolve()

    write_pointer(
        tool_root,
        Pointer(active=Installed(version="2026.8.19", venv="runtimes/yt-dlp/../../../elsewhere")),
    )
    with pytest.raises(RuntimeNotInstalled) as caught:
        manager_for(app_root, FakeUV()).interpreter()
    assert "is not where the pointer says it is" in str(caught.value)


@pytest.mark.parametrize("root", ["relative/app-data", "."])
def test_the_app_data_root_must_be_absolute(root: str) -> None:
    """A guessed root is a runtime nobody asked for, so a relative one is refused."""
    with pytest.raises(ValueError, match="absolute path"):
        RuntimeManager(root=Path(root), settings=RuntimeSettings(), runner=FakeUV())


def test_the_app_data_root_must_be_outside_the_application_folder() -> None:
    """Nothing the user chose lives inside the code, so a root in there is refused."""
    inside = Path(__file__).resolve().parents[2] / "app-data"
    with pytest.raises(ValueError, match="outside the application folder"):
        RuntimeManager(root=inside, settings=RuntimeSettings(), runner=FakeUV())


# -- rollback ---------------------------------------------------------------


def test_rollback_restores_the_earlier_version(app_root: Path, tool_root: Path) -> None:
    """The version before the update becomes active again, and is really the one used."""
    older = make_venv(app_root, "2026.8.19")
    newer = make_venv(app_root, "2026.9.1")
    write_installed(
        app_root, installed("2026.9.1", "2026.09.01"), installed("2026.8.19", VENV_SPELLING)
    )
    manager = manager_for(app_root, FakeUV())
    assert manager.interpreter() == str(newer)

    went_back = manager.rollback()

    assert went_back.version == VENV_SPELLING
    assert manager.interpreter() == str(older)
    pointer = read_pointer(tool_root)
    assert pointer.active.version == VENV_SPELLING
    # The version that was rolled away from is kept, so the user can go forward again.
    assert pointer.previous.version == "2026.09.01"
    assert newer.is_file()


def test_rollback_without_an_earlier_version_is_a_plain_error(app_root: Path) -> None:
    """Nothing to go back to is said plainly, and the pointer is left alone."""
    make_venv(app_root, "2026.9.1")
    write_installed(app_root, installed("2026.9.1", "2026.09.01"), None)
    with pytest.raises(RollbackUnavailable, match="There is no earlier yt-dlp"):
        manager_for(app_root, FakeUV()).rollback()


def test_rollback_when_the_earlier_version_is_gone_is_a_plain_error(
    app_root: Path, tool_root: Path
) -> None:
    """A pointer to a folder that is not there is not a version to go back to."""
    make_venv(app_root, "2026.9.1")
    write_installed(
        app_root, installed("2026.9.1", "2026.09.01"), installed("2026.8.19", VENV_SPELLING)
    )
    before = read_pointer(tool_root)
    with pytest.raises(RollbackUnavailable, match="not on disk any more"):
        manager_for(app_root, FakeUV()).rollback()
    assert read_pointer(tool_root) == before


# -- one update at a time ---------------------------------------------------


def test_a_second_update_is_refused_while_one_is_running(app_root: Path, tool_root: Path) -> None:
    """A jobs-lane rule cannot hold across processes; the lock file does."""
    held = UpdateLock(tool_root, stale_after_s=7200.0)
    held.acquire()
    try:
        with (
            pytest.raises(UpdateInProgress, match="Another yt-dlp update is running"),
            UpdateLock(tool_root, stale_after_s=7200.0),
        ):
            pass  # pragma: no cover - the lock is refused before this runs
    finally:
        held.release()
    assert not (tool_root / LOCK_NAME).exists()


def test_an_update_is_not_started_while_the_lock_is_held(app_root: Path, tool_root: Path) -> None:
    """The refused update leaves the active version exactly as it was."""
    make_venv(app_root, "2026.8.19")
    write_installed(app_root, installed("2026.8.19", VENV_SPELLING), None)
    uv = FakeUV(reported="2026.09.01")
    held = UpdateLock(tool_root, stale_after_s=7200.0)
    held.acquire()
    try:
        with pytest.raises(UpdateInProgress):
            manager_for(app_root, uv).check_and_update(
                TEST_VIDEO_URL, FakeProbe(), client=pypi_client("2026.9.1")
            )
    finally:
        held.release()
    assert read_pointer(tool_root).active.version == VENV_SPELLING
    assert uv.pins_requested == []


def test_a_lock_left_behind_by_a_crash_is_taken_over(app_root: Path, tool_root: Path) -> None:
    """A killed update must not block every later one forever."""
    tool_root.mkdir(parents=True, exist_ok=True)
    path = tool_root / LOCK_NAME
    path.write_text("{}", encoding="utf-8")
    old = time.time() - 3 * 3600
    os.utime(path, (old, old))

    with UpdateLock(tool_root, stale_after_s=7200.0):
        assert path.is_file()
    assert not path.exists()


def test_a_lock_that_is_not_old_is_not_taken_over(app_root: Path, tool_root: Path) -> None:
    """A fresh lock is a running update, whatever the clock says about it."""
    tool_root.mkdir(parents=True, exist_ok=True)
    (tool_root / LOCK_NAME).write_text("{}", encoding="utf-8")
    with pytest.raises(UpdateInProgress, match="Another yt-dlp update is running"):
        UpdateLock(tool_root, stale_after_s=7200.0).acquire()
    assert (tool_root / LOCK_NAME).exists()


def test_the_lock_names_the_process_that_holds_it(app_root: Path, tool_root: Path) -> None:
    """A person who finds the file can see who took it and what for."""
    with UpdateLock(tool_root, stale_after_s=7200.0, purpose="yt-dlp update"):
        held = json.loads((tool_root / LOCK_NAME).read_text(encoding="utf-8"))
    assert held["pid"] == os.getpid()
    assert held["purpose"] == "yt-dlp update"
    assert held["taken_at"].endswith("Z")
