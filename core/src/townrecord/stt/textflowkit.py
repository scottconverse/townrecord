"""Transcribing one meeting's audio on this machine (spec 8.5, 8.9, 8.10).

TextFlowKit runs locally, so a transcript this module produces never leaves
the machine and no hosted model is called (spec 8.10, PROJECT-BRIEF rule 9).
Two facts follow from that, and this module is where they are kept:

* The command is an argument list, never a string for a shell, and the model
  is named in it. The default is ``small``: spec 8.10 trades accuracy for
  time on the machine the user already has.
* The output is read into the same :class:`~townrecord.captions.Segment` and
  :class:`~townrecord.captions.Word` that the caption parser returns. Captions
  and local speech are two sources of one kind of transcript (spec 10.5), so
  there is no second segment type and no second code path that reads one.

Times arrive as float seconds, because that is what TextFlowKit writes. They
are stored as whole milliseconds, rounded half up: a tie at exactly 0.5 ms
goes up, on any machine and in any Python. Python's built-in ``round`` would
send a tie to the even number instead (0.0025 s becomes 2 ms, not 3 ms),
which is a rule about the number rather than about the recording.

A ``hidden`` segment is left out. In TextFlowKit a hidden segment is a
caller's own annotation that nothing in its transcription pipeline sets, and
TextFlowKit itself keeps it out of the transcript text, the exports, the
retrieval index and the searches. Neither the shared segment type nor the
``segments`` table of migration 0005 has a place to keep the mark, and a
segment stored as ordinary speech would read as speech that was never said.
A ``speaker`` label is kept exactly as the transcriber wrote it, and stays
None when it wrote none: spec 10.6 stores that as "an unidentified speaker",
and this module does not invent a name or a number to fill the gap.

Standard library only.
"""

from __future__ import annotations

import json
import math
from typing import Any

from townrecord.captions import CaptionParseError, Segment, Word
from townrecord.captions.parse import normalize_text

__all__ = [
    "DEFAULT_LANGUAGE",
    "DEFAULT_MODEL",
    "TEXTFLOWKIT_PROGRAM",
    "TIMEOUT_FLOOR_S",
    "TIMEOUT_SLOPE",
    "TIMEOUT_UNKNOWN_S",
    "parse_textflowkit_json",
    "textflowkit_argv",
    "transcription_timeout_s",
]

#: The console entry point of TextFlowKit 0.1.6 (``textflowkit.cli:main``).
TEXTFLOWKIT_PROGRAM = "textflowkit"

#: The model of spec 8.10: the small one, run on this machine.
DEFAULT_MODEL = "small"

#: The language of the recordings this service captures.
DEFAULT_LANGUAGE = "en"

#: The time limit grows with the recording: this much of the recording's own
#: length, plus a floor for the loading and the writing either side of it.
TIMEOUT_SLOPE = 1.5
TIMEOUT_FLOOR_S = 600.0

#: When the recording's length is not known. One hour, which is longer than
#: any meeting this service has a length for, and shorter than forever.
TIMEOUT_UNKNOWN_S = 3600.0

#: Milliseconds in a second.
_MILLISECONDS_PER_SECOND = 1000.0


def transcription_timeout_s(audio_seconds: float | None) -> float:
    """How long a local transcription of this recording may take (spec 8.5).

    Args:
        audio_seconds: The length of the audio, or None when the download did
            not report one.

    Returns:
        A number of seconds: ``TIMEOUT_SLOPE`` times the length plus
        ``TIMEOUT_FLOOR_S``, or ``TIMEOUT_UNKNOWN_S`` when the length is not
        known. A recording of no length at all still gets the floor, so the
        model has time to load.

    Raises:
        ValueError: The length is negative, which no recording has.
    """
    if audio_seconds is None:
        return TIMEOUT_UNKNOWN_S
    if audio_seconds < 0:
        raise ValueError(f"a recording has no negative length: {audio_seconds!r}")
    return audio_seconds * TIMEOUT_SLOPE + TIMEOUT_FLOOR_S


def textflowkit_argv(
    audio_path: str,
    out_dir: str,
    model: str = DEFAULT_MODEL,
    language: str = DEFAULT_LANGUAGE,
) -> list[str]:
    """The transcription command of spec 8.5, as an argument list.

    Args:
        audio_path: The audio file the download produced.
        out_dir: The folder the JSON is written to. It is a folder of our
            own, never the one the audio sits in, so a transcript file can
            be read back without guessing which of its neighbours it is.
        model: The TextFlowKit model to run.
        language: The language of the recording.

    Returns:
        The command, ready for :func:`townrecord.proc.run_allowlisted`.
    """
    return [
        TEXTFLOWKIT_PROGRAM,
        "transcribe",
        audio_path,
        "--formats",
        "json",
        "--output-dir",
        out_dir,
        "--model",
        model,
        "--language",
        language,
    ]


def parse_textflowkit_json(data: bytes) -> list[Segment]:
    """Read TextFlowKit's JSON transcript into the shared segment type.

    Args:
        data: The bytes of the ``.json`` file the transcription wrote.

    Returns:
        One :class:`~townrecord.captions.Segment` per spoken segment, in file
        order, with word times when the file has them. Hidden segments and
        segments whose text is empty are left out.

    Raises:
        CaptionParseError: The bytes are not a TextFlowKit transcript, or a
            segment has no usable time or text.
        TypeError: ``data`` is not bytes.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"parse_textflowkit_json needs bytes, got {type(data).__name__}")
    try:
        document = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptionParseError(f"the transcription file is not JSON: {exc}") from None
    if not isinstance(document, dict):
        raise CaptionParseError(
            f"a TextFlowKit transcript is a JSON object, got {type(document).__name__}"
        )
    raw_segments = document.get("segments")
    if not isinstance(raw_segments, list):
        raise CaptionParseError("a TextFlowKit transcript has no list of segments")
    segments: list[Segment] = []
    for index, raw in enumerate(raw_segments):
        segment = _segment(raw, index)
        if segment is not None:
            segments.append(segment)
    return segments


def _segment(raw: Any, index: int) -> Segment | None:
    """Read one segment, or None when the transcript has nothing to store."""
    where = f"segments[{index}]"
    if not isinstance(raw, dict):
        raise CaptionParseError(f"{where} is not an object")
    if raw.get("hidden") is True:
        return None
    text = normalize_text(_text(raw.get("text"), f"{where}.text"))
    if not text:
        return None
    return Segment(
        start_ms=_whole_milliseconds(raw.get("start"), f"{where}.start"),
        end_ms=_whole_milliseconds(raw.get("end"), f"{where}.end"),
        text=text,
        words=_words(raw.get("words"), where),
        speaker=_speaker(raw.get("speaker")),
    )


def _words(raw: Any, where: str) -> tuple[Word, ...]:
    """Read a segment's word times. TextFlowKit writes them as a list."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise CaptionParseError(f"{where}.words is not a list")
    words: list[Word] = []
    for index, entry in enumerate(raw):
        at = f"{where}.words[{index}]"
        if not isinstance(entry, dict):
            raise CaptionParseError(f"{at} is not an object")
        text = normalize_text(_text(entry.get("text"), f"{at}.text"))
        if not text:
            continue
        words.append(
            Word(
                start_ms=_whole_milliseconds(entry.get("start"), f"{at}.start"),
                text=text,
                end_ms=_whole_milliseconds(entry.get("end"), f"{at}.end"),
            )
        )
    return tuple(words)


def _text(value: Any, where: str) -> str:
    """Read a required string, refusing anything else."""
    if not isinstance(value, str):
        raise CaptionParseError(f"{where} is not text")
    return value


def _speaker(value: Any) -> str | None:
    """Read a speaker label as written. Blank means the file named none."""
    if not isinstance(value, str):
        return None
    label = value.strip()
    return label or None


def _whole_milliseconds(value: Any, where: str) -> int:
    """Read a float number of seconds as whole milliseconds, rounded half up."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CaptionParseError(f"{where} is not a number of seconds")
    seconds = float(value)
    if seconds < 0:
        raise CaptionParseError(f"{where} is negative: {seconds!r}")
    return int(math.floor(seconds * _MILLISECONDS_PER_SECOND + 0.5))
