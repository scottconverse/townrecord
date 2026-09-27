"""Installing one yt-dlp version into its own venv (spec 8.9, 8.3).

`uv` is faked here, so nothing is installed and nothing is downloaded. The two
things these tests pin down are the ones the brief calls out: the child is an
argument list with the allow-listed environment (the failure this unit answers
is a child that cannot see a user-site install), and the version recorded is
the one the venv reports, not the one that was asked for.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from townrecord.runtime.manager import (
    RuntimeInstallFailed,
    RuntimeManager,
    RuntimeToolMissing,
)
from townrecord.runtime.settings import TOOL_NAME, RuntimeSettings

from .fakes import VENV_SPELLING, FakeUV, manager_for, venv_python


def test_uv_is_run_as_an_argument_list_with_the_allow_listed_environment(app_root: Path) -> None:
    """No shell, an argument list, and an environment a user-site install is not in."""
    uv = FakeUV()
    manager_for(app_root, uv).install("2026.8.19")

    venv = app_root / "runtimes" / TOOL_NAME / "2026.8.19"
    assert uv.calls[0]["argv"] == ["uv", "venv", str(venv)]
    assert uv.pins_requested == ["yt-dlp==2026.8.19"]

    env = uv.calls[0]["env"]
    # This is the whole bug: the allow-listed child environment has no APPDATA,
    # so a yt-dlp in the user site is invisible to it. PATH is what it does get.
    assert "APPDATA" not in env
    assert "PYTHONPATH" not in env
    assert "PATH" in env
    # The interpreter running this test is never what uv is pointed at.
    assert all(sys.executable not in call for call in uv.calls[0]["argv"])


def test_the_recorded_version_comes_from_the_venv_not_the_request(app_root: Path) -> None:
    """Spec 8.9: ask the venv. The request is a wish; the venv is the fact."""
    uv = FakeUV(reported=VENV_SPELLING)
    result = manager_for(app_root, uv).install("2026.8.19")

    assert result.requested == "2026.8.19"
    assert result.version == VENV_SPELLING
    assert result.matches_request is True
    assert uv.asked == [result.python]
    assert "yt-dlp==2026.8.19" in uv.pins_requested


def test_a_venv_that_reports_another_version_is_recorded_as_it_spoke(app_root: Path) -> None:
    """A venv that holds something else is recorded as holding something else."""
    uv = FakeUV(reported="2026.7.30")
    result = manager_for(app_root, uv).install("2026.8.19")
    assert result.version == "2026.7.30"
    assert result.matches_request is False


def test_the_venv_folder_is_named_from_the_version_asked_for(app_root: Path) -> None:
    """One folder per version, so an update never overwrites what is installed."""
    result = manager_for(app_root, FakeUV()).install("2026.8.19")
    assert result.venv == "runtimes/yt-dlp/2026.8.19"
    assert Path(result.python) == venv_python(app_root / "runtimes" / TOOL_NAME / "2026.8.19")


@pytest.mark.parametrize("version", ["../escape", "with/slash", "", "--flag", "a b"])
def test_a_version_that_is_not_a_plain_word_is_refused(app_root: Path, version: str) -> None:
    """A version becomes a folder name, so it never carries a separator or a dash."""
    uv = FakeUV()
    with pytest.raises(RuntimeInstallFailed, match="not a version TownRecord will install"):
        manager_for(app_root, uv).install(version)
    assert uv.calls == []


def test_the_last_line_a_failed_uv_said_is_the_reason(app_root: Path) -> None:
    """A failure is one plain sentence with uv's own words in it."""
    uv = FakeUV(install_rc=1)
    with pytest.raises(RuntimeInstallFailed) as caught:
        manager_for(app_root, uv).install("2026.8.19")
    assert "uv could not install yt-dlp 2026.8.19" in str(caught.value)
    assert "no such version" in str(caught.value)


def test_a_venv_without_an_interpreter_is_a_failure_and_not_a_runtime(app_root: Path) -> None:
    """A folder uv claims it made, but with no Python in it, is not a runtime."""
    uv = FakeUV(build_python=False)
    with pytest.raises(RuntimeInstallFailed, match="holds no Python interpreter"):
        manager_for(app_root, uv).install("2026.8.19")


def test_a_runtime_that_will_not_say_its_version_is_a_failure(app_root: Path) -> None:
    """A version that cannot be recorded cannot be pinned, so the install fails."""
    uv = FakeUV(version_rc=1, version_stderr="No module named yt_dlp")
    with pytest.raises(RuntimeInstallFailed) as caught:
        manager_for(app_root, uv).install("2026.8.19")
    assert "No module named yt_dlp" in str(caught.value)


def test_the_system_python_is_never_the_one_installed_into(app_root: Path) -> None:
    """The venv is the target, and the interpreter inside it is the one written."""
    uv = FakeUV()
    result = manager_for(app_root, uv).install("2026.8.19")
    assert Path(result.python) != Path(sys.executable)
    assert uv.pins_requested == ["yt-dlp==2026.8.19"]
    assert all(sys.executable not in call["argv"] for call in uv.calls)


def test_uv_is_looked_for_on_path_and_its_absence_is_plain(
    app_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No uv, no install, and one plain sentence naming what to do."""
    monkeypatch.setattr("townrecord.runtime.manager.shutil.which", lambda name: None)
    manager = RuntimeManager(root=app_root, settings=RuntimeSettings(), runner=FakeUV())
    with pytest.raises(RuntimeToolMissing) as caught:
        manager.install("2026.8.19")
    assert "uv was not found" in str(caught.value)
    assert "Install uv" in str(caught.value)


def test_uv_taken_from_the_setting_is_used_as_given(app_root: Path) -> None:
    """The setting is the answer when it is set, and PATH is not consulted."""
    uv = FakeUV()
    manager = RuntimeManager(
        root=app_root, settings=RuntimeSettings(), uv_path="C:/tools/uv.exe", runner=uv
    )
    manager.install("2026.8.19")
    assert uv.calls[0]["argv"][0] == "C:/tools/uv.exe"


def test_a_venv_for_the_same_version_is_rebuilt_rather_than_reused(app_root: Path) -> None:
    """A retry starts from a clean folder, so a half-made venv cannot be trusted."""
    uv = FakeUV()
    manager = manager_for(app_root, uv)
    manager.install("2026.8.19")
    stale = app_root / "runtimes" / TOOL_NAME / "2026.8.19" / "stale.txt"
    stale.write_text("left over", encoding="utf-8")
    manager.install("2026.8.19")
    assert not stale.exists()
    assert len(uv.pins_requested) == 2
