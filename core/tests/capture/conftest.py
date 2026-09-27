"""Fixtures for the caption capture tests: one city, one channel, one video.

The channel's origin is a YouTube one, because that is the origin the download
archive turns into an extractor key. The video is finished, so the tests that
are not about readiness capture it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from townrecord.repo import (
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_source,
    insert_video,
)

from .fakes import PLATFORM_VIDEO_ID

__all__ = ["PLATFORM_VIDEO_ID"]


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    """The storage root the job writes under (spec 8.6)."""
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture
def city(conn: sqlite3.Connection) -> int:
    """The city the channel belongs to."""
    return insert_jurisdiction(
        conn, type="city", name="Longmont", official_id="0845970", official_id_kind="geoid"
    )


@pytest.fixture
def body(conn: sqlite3.Connection, city: int) -> int:
    """The city council."""
    return insert_body(conn, jurisdiction_id=city, name="City Council")


@pytest.fixture
def channel(conn: sqlite3.Connection, city: int, body: int) -> int:
    """The city's video channel, listed on YouTube."""
    return insert_source(
        conn,
        jurisdiction_id=city,
        body_id=body,
        type="video_channel",
        origin="https://www.youtube.com/channel/UC-longmont",
        suggested_by="discovery",
        reason="The city's meeting page links to this channel.",
        status="accepted",
    )


@pytest.fixture
def meeting(conn: sqlite3.Connection, body: int) -> int:
    """The body's regular session."""
    return insert_meeting(
        conn,
        body_id=body,
        title="City Council Regular Session",
        starts_at="2026-09-08T19:00:00-06:00",
    )


@pytest.fixture
def video(conn: sqlite3.Connection, channel: int, meeting: int) -> int:
    """The primary video of the meeting, ready to capture."""
    return insert_video(
        conn,
        source_id=channel,
        meeting_id=meeting,
        platform_video_id=PLATFORM_VIDEO_ID,
        title="City Council Regular Session",
        url=f"https://www.youtube.com/watch?v={PLATFORM_VIDEO_ID}",
        is_primary=True,
        readiness="finished",
    )
