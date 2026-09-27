"""The daily check: install, test, and switch only when the test passes (spec 8.9).

The rule this file exists for is the one in the brief: a version that fails the
known-video test is never made active, and the version that was active stays
exactly where it was, still installed, so a rollback has something to go back
to. The test video is never fetched: the probe is a fake in every test here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from townrecord.runtime.lock import LOCK_NAME
from townrecord.runtime.manager import RuntimeInstallFailed
from townrecord.runtime.pointer import read_pointer

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
def current(app_root: Path) -> Path:
    """The version that is active before the check: the pinned release."""
    python = make_venv(app_root, "2026.8.19")
    write_installed(app_root, installed("2026.8.19", VENV_SPELLING), None)
    return python


@pytest.fixture
def tool_root(app_root: Path) -> Path:
    """The folder that holds every yt-dlp version, and the pointer."""
    return app_root / "runtimes" / "yt-dlp"


def test_a_failed_probe_does_not_switch(app_root: Path, current: Path, tool_root: Path) -> None:
    """The newest version is installed and tested, and is not made active."""
    uv = FakeUV(reported="2026.09.01")
    probe = FakeProbe(passed=False)

    outcome = manager_for(app_root, uv).check_and_update(
        TEST_VIDEO_URL, probe, client=pypi_client("2026.9.1")
    )

    assert outcome.checked == "2026.9.1"
    assert outcome.requested == "2026.9.1"
    assert outcome.switched is False
    assert outcome.failed is True
    assert outcome.active == VENV_SPELLING
    assert TEST_VIDEO_URL in outcome.reason
    assert "failed its test" in outcome.reason
    assert VENV_SPELLING in outcome.reason

    # The pointer never moved, so nothing that runs the capture job sees a
    # different interpreter than it did before the check.
    pointer = read_pointer(tool_root)
    assert pointer.active == installed("2026.8.19", VENV_SPELLING)
    assert pointer.previous is None
    assert manager_for(app_root, uv).interpreter() == str(current)

    # The failing version was installed and left on disk, but the pointer does
    # not name it, so nothing will run it.
    assert probe.seen == [str(venv_python(app_root / "runtimes" / "yt-dlp" / "2026.9.1"))]
    assert (app_root / "runtimes" / "yt-dlp" / "2026.9.1").is_dir()
    assert pointer.active.venv == "runtimes/yt-dlp/2026.8.19"


def test_a_passed_probe_switches_and_keeps_the_previous(
    app_root: Path, current: Path, tool_root: Path
) -> None:
    """A version that captures the known video becomes active; the old one stays."""
    uv = FakeUV(reported="2026.09.01")
    probe = FakeProbe(passed=True)
    manager = manager_for(app_root, uv)

    outcome = manager.check_and_update(TEST_VIDEO_URL, probe, client=pypi_client("2026.9.1"))

    assert outcome.switched is True
    assert outcome.failed is False
    assert outcome.active == "2026.09.01"
    assert outcome.previous == VENV_SPELLING
    assert TEST_VIDEO_URL in outcome.reason
    assert "is now the active version" in outcome.reason

    newer = venv_python(app_root / "runtimes" / "yt-dlp" / "2026.9.1")
    assert probe.seen == [str(newer)]  # the test ran against the new venv
    assert manager.interpreter() == str(newer)

    pointer = read_pointer(tool_root)
    assert pointer.active == installed("2026.9.1", "2026.09.01")
    # Kept installed for rollback (spec 8.9), not deleted.
    assert pointer.previous == installed("2026.8.19", VENV_SPELLING)
    assert current.is_file()


def test_an_update_that_is_already_the_newest_installs_nothing(
    app_root: Path, current: Path, tool_root: Path
) -> None:
    """PyPI says 2026.8.19 and the venv says 2026.08.19: that is not an update."""
    uv = FakeUV()

    outcome = manager_for(app_root, uv).check_and_update(
        TEST_VIDEO_URL, FakeProbe(), client=pypi_client("2026.8.19")
    )

    assert outcome.switched is False
    assert outcome.failed is False
    assert outcome.requested is None
    assert outcome.active == VENV_SPELLING
    assert outcome.reason == "yt-dlp 2026.08.19 is already the newest version."
    assert uv.calls == []  # uv was never run, so nothing was reinstalled
    assert read_pointer(tool_root).active == installed("2026.8.19", VENV_SPELLING)


def test_a_test_that_blows_up_is_a_failed_test(app_root: Path, current: Path) -> None:
    """A probe that raises is a failure with a plain reason, not a crash."""
    probe = FakeProbe(raises=RuntimeError("the video would not play"))

    outcome = manager_for(app_root, FakeUV(reported="2026.09.01")).check_and_update(
        TEST_VIDEO_URL, probe, client=pypi_client("2026.9.1")
    )

    assert outcome.failed is True
    assert outcome.switched is False
    assert "RuntimeError: the video would not play" in outcome.reason


def test_an_install_that_fails_leaves_the_active_version_alone(
    app_root: Path, current: Path, tool_root: Path
) -> None:
    """An install that cannot finish is raised, and the pointer is untouched."""
    uv = FakeUV(install_rc=1)
    with pytest.raises(RuntimeInstallFailed):
        manager_for(app_root, uv).check_and_update(
            TEST_VIDEO_URL, FakeProbe(), client=pypi_client("2026.9.1")
        )
    assert read_pointer(tool_root).active == installed("2026.8.19", VENV_SPELLING)
    assert manager_for(app_root, uv).interpreter() == str(current)


def test_the_first_run_has_nothing_to_keep_and_still_switches(app_root: Path) -> None:
    """With no version installed yet, a passing test is the whole answer."""
    outcome = manager_for(app_root, FakeUV(reported="2026.09.01")).check_and_update(
        TEST_VIDEO_URL, FakeProbe(passed=True), client=pypi_client("2026.9.1")
    )
    assert outcome.switched is True
    assert outcome.previous is None
    assert outcome.active == "2026.09.01"


def test_the_lock_is_free_again_after_a_check(
    app_root: Path, current: Path, tool_root: Path
) -> None:
    """A finished check, even a failed one, does not leave the update blocked."""
    manager_for(app_root, FakeUV()).check_and_update(
        TEST_VIDEO_URL, FakeProbe(), client=pypi_client("2026.8.19")
    )
    assert not (tool_root / LOCK_NAME).exists()
