"""Fixtures for the private runtime tests.

The app-data root is injected here exactly as the brief requires (spec 14.4):
``tmp_path`` is absolute and outside the application folder, and no test ever
falls back to a default inside the repository.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: The known video the daily check tests a new yt-dlp against (spec 8.9). No
#: test here ever fetches it: the probe is a fake in every unit test.
TEST_VIDEO_URL = "https://www.youtube.com/watch?v=abc123XYZ"


@pytest.fixture
def app_root(tmp_path: Path) -> Path:
    """The app-data root the user chose (spec 14.4)."""
    root = tmp_path / "app-data"
    root.mkdir()
    return root
