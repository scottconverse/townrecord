"""Search a real meeting, read from a caption file that is kept in place.

The fixture is the publisher's own srv3 caption track for the Longmont City
Council regular session of 2026-09-08, held in the oversight repository rather
than copied here. It is read where it lies and never written to, so this test
cannot alter the evidence another unit is checking.

The file belongs to the machine, not to the repository, so the test skips when
it is not there. The skip message names every path that was probed, because
"the fixture is missing" is only useful with the list of places that were
looked in (rule: name the instrument and its blind spot).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from townrecord.captions import parse_srv3
from townrecord.repo import (
    SEGMENT,
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_segment,
    insert_source,
    insert_transcript,
    insert_video,
    search,
)

from .conftest import PutArtifact

#: The caption track for the 2026-09-08 regular session.
FIXTURE_NAME = "3qfQAkAAC9U.en.srv3"

#: Where the file is looked for, in order. The first is the copy held by the
#: oversight repository next to this worktree.
CANDIDATES = (
    Path(__file__).resolve().parents[3].parent
    / "townrecord-oversight"
    / "evidence"
    / "fixtures"
    / "youtube"
    / "captions"
    / FIXTURE_NAME,
    Path(__file__).resolve().parents[3].parent
    / "townrecord-oversight"
    / "evidence"
    / "fixtures"
    / "youtube"
    / "captions-sep22"
    / FIXTURE_NAME,
)


def fixture_path() -> Path | None:
    """Return the caption file, from the environment or from a known place."""
    named = os.environ.get("TOWNRECORD_SEPT8_SRV3", "").strip()
    paths = (Path(named), *CANDIDATES) if named else CANDIDATES
    for path in paths:
        if path.is_file():
            return path
    return None


def test_searching_a_real_meeting_for_an_ordinance(
    conn: sqlite3.Connection, put_artifact: PutArtifact, capsys: pytest.CaptureFixture[str]
) -> None:
    """Search 'ordinance 2026-54' in the real Sept 8 captions and report the times."""
    path = fixture_path()
    if path is None:
        probed = "\n".join(f"  {candidate}" for candidate in CANDIDATES)
        pytest.skip(f"{FIXTURE_NAME} was not found. Paths probed:\n{probed}")

    county = insert_jurisdiction(conn, type="county", name="Boulder County")
    city = insert_jurisdiction(conn, type="city", name="Longmont", parent_id=county)
    body = insert_body(conn, jurisdiction_id=city, name="City Council")
    source = insert_source(
        conn,
        jurisdiction_id=city,
        body_id=body,
        type="video_channel",
        origin="https://videos.test.invalid/channel/UC-longmont",
        suggested_by="discovery",
        reason="The fixture was captured from it.",
        status="accepted",
    )
    meeting = insert_meeting(
        conn,
        body_id=body,
        title="City Council Regular Session",
        starts_at="2026-09-08T19:00:00-06:00",
    )
    video = insert_video(
        conn,
        source_id=source,
        meeting_id=meeting,
        platform_video_id="3qfQAkAAC9U",
        is_primary=True,
    )
    raw = path.read_bytes()
    artifact = put_artifact("transcript", raw, "srv3")
    transcript = insert_transcript(
        conn, video_id=video, artifact_id=artifact, origin="publisher_captions"
    )
    segments = parse_srv3(raw)
    assert segments, f"{path} parsed to no segments"
    for segment in segments:
        insert_segment(
            conn,
            transcript_id=transcript,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            text=segment.text,
        )
    conn.commit()

    hits = search(conn, "ordinance 2026-54")

    reported = [(hit.start_ms, hit.excerpt) for hit in hits]
    with capsys.disabled():
        print(f"\n{path.name}: {len(segments)} segments, {len(hits)} hits")
        for start_ms, excerpt in reported:
            print(f"  {start_ms / 1000:.3f}s  {excerpt}")

    assert hits, "the ordinance is named in this meeting and the search found nothing"
    assert {hit.scope for hit in hits} == {SEGMENT}
    starts = {hit.start_ms for hit in hits}
    # The coordinator's own read of this file put the two spoken namings of the
    # ordinance at 3978 s and 4293 s. The search has to find the same seconds.
    assert 3_978_160 in starts, starts
    assert 4_293_040 in starts, starts
    for hit in hits:
        assert hit.citation.transcript_origin == "publisher_captions"
        assert hit.citation.platform_video_id == "3qfQAkAAC9U"
        assert hit.citation.excerpt == hit.excerpt
