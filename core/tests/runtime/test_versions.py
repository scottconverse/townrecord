"""Comparing the two spellings of one yt-dlp release (spec 8.9).

These tests exist because the difference is real and was measured: PyPI said
`2026.8.19` for the release whose own venv said `2026.08.19`. Text comparison
would call every fresh install out of date, and the daily check would reinstall
the same release every single day.
"""

from __future__ import annotations

import pytest

from townrecord.runtime import versions

from .fakes import PYPI_SPELLING, VENV_SPELLING


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (PYPI_SPELLING, VENV_SPELLING),
        (VENV_SPELLING, PYPI_SPELLING),
        ("2026.8.19", "2026.8.19"),
    ],
)
def test_the_same_release_spelled_two_ways_is_one_release(left: str, right: str) -> None:
    """A leading zero in a part does not make a different release."""
    assert versions.same_version(left, right) is True
    assert versions.is_newer(left, right) is False


def test_a_later_release_is_newer_even_across_spellings() -> None:
    """The comparison is numeric, so the two spellings still order correctly."""
    assert versions.is_newer("2026.9.1", VENV_SPELLING) is True
    assert versions.is_newer("2026.10.1", "2026.9.1") is True
    assert versions.is_newer(VENV_SPELLING, "2026.9.1") is False


def test_a_fourth_part_makes_a_later_build() -> None:
    """A master-branch build carries a fourth part and is later than the release."""
    assert versions.version_key("2026.8.19.232914") == (2026, 8, 19, 232914)
    assert versions.is_newer("2026.8.19.232914", VENV_SPELLING) is True


def test_text_without_a_number_falls_back_to_text() -> None:
    """An unknown shape has no order, so it is only compared as text."""
    assert versions.version_key("nightly") is None
    assert versions.same_version("nightly", "nightly") is True
    assert versions.same_version("nightly", "2026.8.19") is False
