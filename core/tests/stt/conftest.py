"""Fixtures for the local speech to text building blocks (spec 8.5, 8.10).

The transcript JSON below is written by hand from TextFlowKit 0.1.6's own
model (``textflowkit/core/model.py``), whose ``Transcript.to_dict`` writes
source, language, segments, platform, duration, engine and metadata, and whose
segments write start, end, text, speaker, translated_text, hidden and words.
Every time is a float number of seconds. No model is downloaded and no
transcriber runs: these tests read the file format, not the tool.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from townrecord.artifacts import store
from townrecord.repo import (
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_source,
    insert_video,
)


#: A call that builds one TextFlowKit segment object.
def word(start: float, end: float, text: str) -> dict[str, Any]:
    """One word timing, as TextFlowKit's ``WordTiming.to_dict`` writes it."""
    return {"start": start, "end": end, "text": text}


def segment(  # noqa: PLR0913 - one keyword per field the model writes
    start: float,
    end: float,
    text: str,
    *,
    speaker: str | None = None,
    hidden: bool = False,
    words: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """One segment, as TextFlowKit's ``Segment.to_dict`` writes it."""
    return {
        "start": start,
        "end": end,
        "text": text,
        "speaker": speaker,
        "translated_text": None,
        "hidden": hidden,
        "words": list(words),
    }


def transcript_bytes(segments: Iterable[dict[str, Any]], **overrides: Any) -> bytes:
    """One transcript file, as TextFlowKit's ``Transcript.to_dict`` writes it."""
    document: dict[str, Any] = {
        "source": "meeting.opus",
        "language": "en",
        "segments": list(segments),
        "platform": None,
        "duration": 3600.0,
        "engine": "faster-whisper",
        "metadata": {"model": "small", "device": "cpu"},
    }
    document.update(overrides)
    return json.dumps(document).encode("utf-8")


@pytest.fixture
def video(conn: sqlite3.Connection) -> int:
    """A city, a body, a meeting and its video: somewhere to store a transcript."""
    city = insert_jurisdiction(conn, type="city", name="Longmont")
    body = insert_body(conn, jurisdiction_id=city, name="City Council")
    meeting = insert_meeting(
        conn,
        body_id=body,
        title="City Council Regular Session",
        starts_at="2026-09-08T19:00:00-06:00",
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
    return insert_video(
        conn,
        source_id=channel,
        meeting_id=meeting,
        platform_video_id="v-0001",
        title="City Council Regular Session",
        is_primary=True,
    )


@pytest.fixture
def transcript_artifact(conn: sqlite3.Connection, tmp_path: Path) -> int:
    """A local transcript file of the fixture's video, in the real artifact store.

    It is the JSON the transcriber writes, stored as the transcript it is
    (spec 8.6): a transcript row points at an artifact, never at a path.
    """
    root = tmp_path / "storage"
    root.mkdir(exist_ok=True)
    payload = transcript_bytes([segment(0.0, 1.0, "Good evening.")])
    return store(conn, root, "transcript", payload, "json").id
