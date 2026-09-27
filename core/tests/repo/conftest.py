"""One small area and its artifacts, for the core model tests (spec 6.2).

The area is the size of the object list in the spec and no larger: a county, a
city inside it, one body, one portal, one video channel, one meeting. Files are
written through the real artifact store of migration 0004, so a test that points
a record at an artifact points at a row that exists for the reason it would in
the running system.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from townrecord.artifacts import store
from townrecord.repo import (
    add_overlap,
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_source,
    insert_transcript,
    insert_video,
)


@dataclass(frozen=True)
class Area:
    """The ids of the fixtures, in the order they were stored."""

    county: int
    city: int
    body: int
    portal: int
    channel: int
    meeting: int


#: A call that stores bytes and answers with an artifact id.
PutArtifact = Callable[[str, bytes, str], int]


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    """The storage root the artifacts are written under (spec 8.6).

    The store wants an absolute path that already exists.
    """
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture
def put_artifact(conn: sqlite3.Connection, storage_root: Path) -> PutArtifact:
    """Store bytes and return the artifact id. Different bytes, different row."""

    def put(kind: str, payload: bytes, ext: str = "bin") -> int:
        return store(conn, storage_root, kind, payload, ext).id

    return put


@pytest.fixture
def area(conn: sqlite3.Connection) -> Area:
    """A county, a city that overlaps it, a body, two sources and a meeting."""
    county = insert_jurisdiction(
        conn, type="county", name="Boulder County", official_id="08013", official_id_kind="fips"
    )
    city = insert_jurisdiction(
        conn,
        type="city",
        name="Longmont",
        official_id="0845970",
        official_id_kind="geoid",
        parent_id=county,
    )
    add_overlap(conn, city, county)
    body = insert_body(conn, jurisdiction_id=city, name="City Council")
    portal = insert_source(
        conn,
        jurisdiction_id=city,
        body_id=body,
        type="meeting_portal",
        origin="https://portal.test.invalid",
        suggested_by="discovery",
        reason="The city's meeting page links to it.",
        status="accepted",
    )
    channel = insert_source(
        conn,
        jurisdiction_id=city,
        type="video_channel",
        origin="https://videos.test.invalid/channel/UC-test",
        suggested_by="discovery",
        reason="The portal lists this channel for its recordings.",
        status="accepted",
    )
    meeting = insert_meeting(
        conn,
        body_id=body,
        title="City Council Regular Session",
        starts_at="2026-09-08T19:00:00-06:00",
    )
    return Area(
        county=county, city=city, body=body, portal=portal, channel=channel, meeting=meeting
    )


@pytest.fixture
def video(conn: sqlite3.Connection, area: Area) -> int:
    """The primary video of the meeting, listed on the channel source."""
    return insert_video(
        conn,
        source_id=area.channel,
        meeting_id=area.meeting,
        platform_video_id="v-0001",
        title="City Council Regular Session",
        is_primary=True,
    )


@pytest.fixture
def transcript_artifact(put_artifact: PutArtifact) -> int:
    """A transcript file in the store (spec 8.6)."""
    return put_artifact(
        "transcript", b"WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nGood evening.\n", "vtt"
    )


@pytest.fixture
def document_artifact(put_artifact: PutArtifact) -> int:
    """A meeting document in the store. One page, no footer number."""
    return put_artifact("document", b"%PDF-1.4\n% one page of an agenda\n", "pdf")


@pytest.fixture
def transcript(conn: sqlite3.Connection, video: int, transcript_artifact: int) -> int:
    """A publisher-captions transcript of the video (spec 8.7)."""
    return insert_transcript(
        conn, video_id=video, artifact_id=transcript_artifact, origin="publisher_captions"
    )
