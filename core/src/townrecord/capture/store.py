"""Where a transcript and its lines are written, whichever path produced them.

Captions arrive as a file from yt-dlp (spec 8.3) and a local transcription
arrives as a JSON file from TextFlowKit (spec 8.5). They are two sources of one
kind of transcript (spec 10.5), and spec 8.5 is explicit that the second does
not get a writer of its own:

    "TextFlowKit output goes into the same transcript path as captions, with no
    second code path."

So both callers reach the database through the one function below, and a change
to how a transcript is written lands on both at once.

A local transcription also carries a :class:`~townrecord.stt.provenance.Provenance`
(spec 8.5, 8.6): the tool, its version, the model, the device and the SHA-256 of
the audio the model heard. The ``transcripts`` table of migration 0005 has no
column for it, so the record is kept in the metadata of the artifact the
transcript points at, which is where an artifact keeps what it knows about its
own bytes. It is written in the same transaction as the row.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import suppress
from typing import Any

from .. import artifacts, repo
from ..captions.parse import Segment
from ..stt.provenance import Provenance

__all__ = ["insert_transcript_with_segments", "provenance_meta"]


def provenance_meta(provenance: Provenance) -> dict[str, Any]:
    """Return a provenance record as the artifact metadata stores it.

    The keys are written out one by one rather than taken from the dataclass,
    so a field added to
    :class:`~townrecord.stt.provenance.Provenance` later does not silently
    change what an artifact on the user's disk says about itself.
    """
    return {
        "tool": provenance.tool,
        "tool_version": provenance.tool_version,
        "model": provenance.model,
        "device": provenance.device,
        "audio_sha256": provenance.audio_sha256,
        "origin": provenance.origin,
    }


def insert_transcript_with_segments(
    conn: sqlite3.Connection,
    *,
    video_id: int,
    artifact_id: int,
    origin: str,
    segments: Sequence[Segment],
    provenance: Provenance | None = None,
    is_provisional: bool = True,
    settled_under_churn: bool = False,
    settled_at: str | None = None,
) -> int:
    """Store a transcript and its lines in one transaction, and return its id.

    Half a transcript is worse than none: a row whose lines are missing reads
    like a meeting where nobody spoke. So the two writes are one transaction
    and either both land or neither does (spec 8.7, PROJECT-BRIEF rule F).

    The capture is provisional by default: the 24 hour rule of spec 8.7 is what
    makes a capture final, and the pass that rechecks it is a later unit. The
    three flags are here for the one caller that writes a row it did not make
    itself: the sister-channel fallback of spec 8.4.1 copies the state of the
    transcript it was handed, rather than pretending a transcript that has
    already settled is still inside its 24 hours.

    Args:
        conn: The open database.
        video_id: The video the transcript belongs to.
        artifact_id: The stored artifact holding the bytes this was read from.
        origin: The origin of the transcript, as migration 0005 spells it.
        segments: The timed lines, in file order.
        provenance: What made a locally transcribed transcript, or None for a
            capture from captions. A provenance knows its own origin, and it is
            the same one the row is stored with.
        is_provisional: True while the 24 hour window of spec 8.7 is open.
        settled_under_churn: True when the capture settled while the source was
            still changing.
        settled_at: When the capture settled, or None while it is provisional.
            The schema allows one of the two, never both and never neither.

    Raises:
        ValueError: A provenance was given whose origin is not the origin the
            transcript is being stored with. Two answers to one question is a
            bug in the caller, not something to write down.
    """
    if provenance is not None and origin != provenance.origin:
        raise ValueError(
            f"This transcript is stored with origin {origin!r} and the provenance says "
            f"{provenance.origin!r}; a locally transcribed transcript has one origin."
        )
    conn.execute("BEGIN IMMEDIATE")
    try:
        transcript_id = repo.insert_transcript(
            conn,
            video_id=video_id,
            artifact_id=artifact_id,
            origin=origin,
            is_provisional=is_provisional,
            settled_under_churn=settled_under_churn,
            settled_at=settled_at,
        )
        for segment in segments:
            repo.insert_segment(
                conn,
                transcript_id=transcript_id,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.text,
                speaker_label=None,
            )
        if provenance is not None:
            artifacts.merge_meta(conn, artifact_id, {"provenance": provenance_meta(provenance)})
        conn.execute("COMMIT")
    except BaseException:
        with suppress(sqlite3.Error):  # nothing to roll back
            conn.execute("ROLLBACK")
        raise
    return transcript_id
