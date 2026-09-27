"""Storage root checks (spec 8.6)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from townrecord.storage import application_folder, check_storage_root


def test_no_folder_given_is_rejected() -> None:
    result = check_storage_root(None)
    assert result.ok is False
    assert result.reason == "No folder was given."


def test_empty_path_is_rejected() -> None:
    result = check_storage_root("   ")
    assert result.ok is False
    assert result.reason == "No folder was given."


def test_relative_path_is_rejected() -> None:
    result = check_storage_root("captures")
    assert result.ok is False
    assert result.reason == "The folder must be an absolute path."


def test_path_inside_the_application_folder_is_rejected(tmp_path: Path) -> None:
    app_folder = tmp_path / "app"
    result = check_storage_root(app_folder / "storage", app_folder=app_folder)
    assert result.ok is False
    assert result.reason == "The folder is inside the application folder."


def test_the_application_folder_itself_is_rejected(tmp_path: Path) -> None:
    app_folder = tmp_path / "app"
    result = check_storage_root(app_folder, app_folder=app_folder)
    assert result.ok is False
    assert result.reason == "The folder is inside the application folder."


def test_the_real_application_folder_is_rejected() -> None:
    result = check_storage_root(application_folder() / "storage")
    assert result.ok is False
    assert result.reason == "The folder is inside the application folder."


def test_a_file_is_rejected(tmp_path: Path) -> None:
    a_file = tmp_path / "not-a-folder.txt"
    a_file.write_text("hello", encoding="utf-8")
    result = check_storage_root(a_file)
    assert result.ok is False
    assert result.reason == "That path is a file, not a folder."


def test_a_folder_that_cannot_be_created_is_rejected(tmp_path: Path) -> None:
    a_file = tmp_path / "blocker"
    a_file.write_text("hello", encoding="utf-8")
    result = check_storage_root(a_file / "under-a-file")
    assert result.ok is False
    assert result.reason is not None
    assert result.reason.startswith("The folder could not be created:")


@pytest.mark.skipif(os.name == "nt", reason="chmod does not make a folder unwritable on Windows")
def test_a_folder_that_cannot_be_written_is_rejected(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        result = check_storage_root(locked)
    finally:
        locked.chmod(0o700)
    assert result.ok is False
    assert result.reason is not None
    assert result.reason.startswith("The folder is not writable:")


def test_a_good_folder_is_accepted_and_left_clean(tmp_path: Path) -> None:
    chosen = tmp_path / "captures"
    result = check_storage_root(chosen)
    assert result.ok is True
    assert result.reason is None
    assert result.path == chosen.resolve()
    assert chosen.is_dir()
    assert list(chosen.iterdir()) == []


def test_a_good_folder_is_accepted_when_it_already_exists(tmp_path: Path) -> None:
    chosen = tmp_path / "captures"
    chosen.mkdir()
    result = check_storage_root(str(chosen))
    assert result.ok is True
    assert list(chosen.iterdir()) == []
