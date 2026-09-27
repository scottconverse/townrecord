"""What a locally transcribed transcript was made with (spec 8.5, 8.6, 10.5).

A transcript of this kind is evidence, so the record kept with it says which
tool made it, which version of that tool, which model, which device, and the
SHA-256 of the exact audio the model heard. That last one is the whole point
of the record: the same audio bytes can be transcribed again, by this machine
or another one, and the two transcripts compared.

The origin is fixed, not a parameter. Everything this package produces is a
local transcription, so the origin is ``local_speech_to_text`` and nothing
else; a caller that wants to store captions is storing a capture of a
different kind and has no use for this record.

That spelling is the schema's own. Migration ``0005_core_model.sql`` line 256
is:

    origin IN ('publisher_captions', 'auto_captions', 'sister_channel', 'local_speech_to_text')

Spec 10.5 calls the same thing "local speech-to-text" in prose. The stored
value is the one in the constraint, and this module names it once.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

__all__ = [
    "LOCAL_SPEECH_TO_TEXT",
    "Provenance",
    "ProvenanceError",
    "sha256_of_audio",
]

#: The origin of a transcript made on this machine, as migration 0005 stores
#: it: ``local_speech_to_text``.
LOCAL_SPEECH_TO_TEXT = "local_speech_to_text"

#: The console entry point of the transcriber (see :mod:`.textflowkit`).
TEXTFLOWKIT_TOOL = "textflowkit"

#: The length of a SHA-256 digest, in hexadecimal characters.
_DIGEST_HEX_LENGTH = 64

_HEX_DIGITS = frozenset("0123456789abcdef")


class ProvenanceError(ValueError):
    """A provenance record could not be built. The message says why."""


def sha256_of_audio(data: bytes) -> str:
    """The SHA-256 of an audio file's bytes, in lowercase hexadecimal."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"sha256_of_audio needs bytes, got {type(data).__name__}")
    return hashlib.sha256(bytes(data)).hexdigest()


@dataclass(frozen=True)
class Provenance:
    """What made a locally transcribed transcript, and of what audio.

    ``device`` is None when the transcriber did not report one. That is not
    "the CPU": it is a fact the run did not state, and it is left unknown
    (PROJECT-BRIEF rule F).
    """

    tool_version: str
    model: str
    audio_sha256: str
    device: str | None = None
    tool: str = TEXTFLOWKIT_TOOL
    origin: str = LOCAL_SPEECH_TO_TEXT

    def __post_init__(self) -> None:
        if not self.tool_version.strip():
            raise ProvenanceError("provenance needs the version of the tool that ran")
        if not self.model.strip():
            raise ProvenanceError("provenance needs the model that ran")
        if self.origin != LOCAL_SPEECH_TO_TEXT:
            raise ProvenanceError(
                "a locally transcribed transcript has one origin, "
                f"{LOCAL_SPEECH_TO_TEXT!r}, not {self.origin!r}"
            )
        digest = self.audio_sha256.strip().lower()
        if len(digest) != _DIGEST_HEX_LENGTH or not set(digest) <= _HEX_DIGITS:
            raise ProvenanceError(f"{self.audio_sha256!r} is not a SHA-256 digest")
        if digest != self.audio_sha256:
            object.__setattr__(self, "audio_sha256", digest)
