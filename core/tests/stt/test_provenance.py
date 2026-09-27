"""What a local transcript was made with, and under which origin (spec 8.5, 10.5).

The origin is a value the database itself checks. These tests read migration
0005's constraint out of the shipped file, and then store a transcript under
the value this package names, so the spelling is proved against the schema
rather than against anybody's prose.
"""

from __future__ import annotations

import sqlite3

import pytest

from townrecord.db import migrations_dir
from townrecord.repo import insert_transcript
from townrecord.stt import LOCAL_SPEECH_TO_TEXT, Provenance, ProvenanceError, sha256_of_audio

#: The migration that fixes the transcript origins.
CORE_MODEL_MIGRATION = "0005_core_model.sql"

#: The audio the record below was made from, and its SHA-256 written out by
#: hand, so the helper is held to the algorithm and not to itself.
AUDIO = b"OggS the audio of one meeting"
AUDIO_SHA256 = "2112d4fdae0d36787fcf7734bab59d10aaf474caaee048d3803edb151638a41c"


def test_the_origin_is_the_value_migration_0005_allows() -> None:
    """Read the constraint, then hold the constant to it.

    Spec 10.5 says "local speech-to-text" in prose. The stored value has
    hyphens replaced by underscores, and the constraint is the authority.
    """
    sql = (migrations_dir() / CORE_MODEL_MIGRATION).read_text(encoding="utf-8")
    constraint = next(
        line.strip() for line in sql.splitlines() if line.strip().startswith("origin IN (")
    )
    assert constraint == (
        "origin IN ('publisher_captions', 'auto_captions', 'sister_channel', "
        "'local_speech_to_text')"
    )
    assert LOCAL_SPEECH_TO_TEXT == "local_speech_to_text"
    assert f"'{LOCAL_SPEECH_TO_TEXT}'" in constraint


def test_a_local_transcript_is_stored_under_that_origin(
    conn: sqlite3.Connection, video: int, transcript_artifact: int
) -> None:
    """A real insert, and the prose spelling is the one the schema refuses."""
    provenance = Provenance(
        tool_version="0.1.6", model="small", device="cpu", audio_sha256=sha256_of_audio(AUDIO)
    )
    transcript = insert_transcript(
        conn, video_id=video, artifact_id=transcript_artifact, origin=provenance.origin
    )
    row = conn.execute("SELECT origin FROM transcripts WHERE id = ?", (transcript,)).fetchone()
    assert row["origin"] == LOCAL_SPEECH_TO_TEXT

    with pytest.raises(sqlite3.IntegrityError):
        insert_transcript(
            conn, video_id=video, artifact_id=transcript_artifact, origin="local speech-to-text"
        )


def test_provenance_records_the_tool_the_model_the_device_and_the_audio() -> None:
    """Five facts, one of them the exact audio the model heard."""
    provenance = Provenance(
        tool_version="0.1.6", model="small", device="cuda", audio_sha256=sha256_of_audio(AUDIO)
    )
    assert provenance.tool == "textflowkit"
    assert provenance.tool_version == "0.1.6"
    assert provenance.model == "small"
    assert provenance.device == "cuda"
    assert provenance.audio_sha256 == sha256_of_audio(AUDIO)
    assert provenance.origin == LOCAL_SPEECH_TO_TEXT


def test_a_device_the_run_did_not_report_stays_unknown() -> None:
    """Not reported is not "the CPU" (PROJECT-BRIEF rule F)."""
    provenance = Provenance(
        tool_version="0.1.6", model="small", audio_sha256=sha256_of_audio(AUDIO)
    )
    assert provenance.device is None


@pytest.mark.parametrize(
    "audio_sha256",
    ["", "not a digest", "e2b0", sha256_of_audio(AUDIO).upper() + "0", "z" * 64],
)
def test_something_that_is_not_a_sha256_is_refused(audio_sha256: str) -> None:
    with pytest.raises(ProvenanceError):
        Provenance(tool_version="0.1.6", model="small", audio_sha256=audio_sha256)


@pytest.mark.parametrize(
    ("tool_version", "model"),
    [("", "small"), ("   ", "small"), ("0.1.6", ""), ("0.1.6", "  ")],
)
def test_provenance_needs_the_tool_version_and_the_model(tool_version: str, model: str) -> None:
    with pytest.raises(ProvenanceError):
        Provenance(tool_version=tool_version, model=model, audio_sha256=sha256_of_audio(AUDIO))


def test_a_local_transcript_cannot_claim_another_origin() -> None:
    """This record describes a local transcription and nothing else."""
    with pytest.raises(ProvenanceError):
        Provenance(
            tool_version="0.1.6",
            model="small",
            audio_sha256=sha256_of_audio(AUDIO),
            origin="publisher_captions",
        )


def test_the_same_audio_has_the_same_hash() -> None:
    """The digest is of the bytes, so it is the same on every run and machine."""
    assert sha256_of_audio(AUDIO) == AUDIO_SHA256
    assert sha256_of_audio(bytes(AUDIO)) == AUDIO_SHA256
    assert sha256_of_audio(AUDIO) != sha256_of_audio(AUDIO + b"\x00")
    assert len(sha256_of_audio(AUDIO)) == 64


def test_a_digest_written_in_upper_case_is_stored_in_lower_case() -> None:
    provenance = Provenance(
        tool_version="0.1.6", model="small", audio_sha256=sha256_of_audio(AUDIO).upper()
    )
    assert provenance.audio_sha256 == sha256_of_audio(AUDIO)


def test_the_audio_must_be_bytes() -> None:
    with pytest.raises(TypeError):
        sha256_of_audio("OggS")  # type: ignore[arg-type]
