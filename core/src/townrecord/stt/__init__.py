"""Local speech to text: the last fallback when there are no captions.

Spec 8.4 orders the sources of a transcript, and this package is the third
one: the audio of a meeting is downloaded, transcribed on this machine, and
stored as a transcript like any other (spec 8.5, 8.10). Nothing here runs a
tool or touches a database; these are the building blocks a job handler uses.

* :mod:`~townrecord.stt.audio` says why the audio is being downloaded, from a
  closed set of reasons, and builds the download command.
* :mod:`~townrecord.stt.textflowkit` builds the transcription command, says
  how long it may take, and reads its JSON into the shared segment type.
* :mod:`~townrecord.stt.provenance` records what the transcript was made with,
  including the SHA-256 of the audio that was transcribed.

Everything a model produces arrives as :class:`~townrecord.captions.Segment`,
the type the caption parser returns as well: one transcript path, one segment
type, whether the words came from a caption track or from a local model
(spec 10.5).
"""

from __future__ import annotations

from townrecord.stt.audio import (
    AUDIO_OUTPUT_TEMPLATE,
    AUDIO_TRIGGER_REASONS,
    AudioRequest,
    AudioRequestError,
    AudioTrigger,
    NotAnAudioTrigger,
    audio_download_argv,
)
from townrecord.stt.provenance import (
    LOCAL_SPEECH_TO_TEXT,
    Provenance,
    ProvenanceError,
    sha256_of_audio,
)
from townrecord.stt.textflowkit import (
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    TEXTFLOWKIT_PROGRAM,
    TIMEOUT_FLOOR_S,
    TIMEOUT_SLOPE,
    TIMEOUT_UNKNOWN_S,
    parse_textflowkit_json,
    textflowkit_argv,
    transcription_timeout_s,
)

__all__ = [
    "AUDIO_OUTPUT_TEMPLATE",
    "AUDIO_TRIGGER_REASONS",
    "AudioRequest",
    "AudioRequestError",
    "AudioTrigger",
    "DEFAULT_LANGUAGE",
    "DEFAULT_MODEL",
    "LOCAL_SPEECH_TO_TEXT",
    "NotAnAudioTrigger",
    "Provenance",
    "ProvenanceError",
    "TEXTFLOWKIT_PROGRAM",
    "TIMEOUT_FLOOR_S",
    "TIMEOUT_SLOPE",
    "TIMEOUT_UNKNOWN_S",
    "audio_download_argv",
    "parse_textflowkit_json",
    "sha256_of_audio",
    "textflowkit_argv",
    "transcription_timeout_s",
]
