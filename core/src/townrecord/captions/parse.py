"""Turn YouTube caption files into timed segments.

Two formats, one output (spec 6.2 "Transcript and Segment", spec 8.3):

* srv3, the XML caption track yt-dlp writes with ``--sub-format srv3``.
* WebVTT, including the inline word timing YouTube uses in auto-captions.

Rules this module follows:

* Times come from the file and are never invented. An srv3 segment ends at
  ``t + d``; a VTT segment ends at its cue end.
* Caption text is kept as captioned. No spelling fixes, because spec 10.1 and
  10.6 read the raw text. Only whitespace is normalized: runs of whitespace and
  line breaks in a cue become one space, and the ends are trimmed.
* A file this module cannot read raises one :class:`CaptionParseError` whose
  message says why. It never returns a partly parsed list.
* Segments whose text is empty after trimming are dropped. In srv3 those are
  the ``a="1"`` line-append markers, which repeat no speech.

Standard library only.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

__all__ = [
    "CaptionParseError",
    "Segment",
    "Word",
    "parse_srv3",
    "parse_vtt",
]

#: One run of whitespace, including the newlines inside a cue.
_WHITESPACE = re.compile(r"\s+")

#: A whole, non-negative number of milliseconds, as srv3 writes attributes.
_WHOLE_NUMBER = re.compile(r"\A\d+\Z")

#: Any markup tag, used to read a cue's visible text.
_TAG = re.compile(r"<[^>]*>")

#: A YouTube inline word time inside cue text: <00:00:01.234>.
_INLINE_TIME = re.compile(r"<(\d{1,3}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})>")

#: A VTT timestamp: HH:MM:SS.mmm or MM:SS.mmm.
_VTT_TIME = re.compile(r"\A(?:(\d{1,3}):)?(\d{2}):(\d{2})\.(\d{3})\Z")

#: A VTT cue timing line, with any cue settings after the end time.
_VTT_TIMING_LINE = re.compile(
    r"\A(\d{1,3}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
    r"\s*-->\s*"
    r"(\d{1,3}:\d{2}:\d{2}\.\d{3}|\d{2}:\d{2}\.\d{3})"
    r"(?:\s+(.*))?\Z"
)

#: The character references WebVTT defines. Nothing else is decoded. The left to
#: right mark, the right to left mark and the no break space are written as
#: escapes, so the source stays plain ASCII.
_VTT_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&lrm;": "\u200e",
    "&rlm;": "\u200f",
    "&nbsp;": "\u00a0",
}

_NUMERIC_ENTITY = re.compile(r"&#(?:(\d+)|[xX]([0-9a-fA-F]+));")

#: Blocks that carry no cue.
_VTT_NON_CUE_BLOCKS = ("NOTE", "STYLE", "REGION")


class CaptionParseError(ValueError):
    """A caption file could not be read. The message says why."""


@dataclass(frozen=True)
class Word:
    """One captioned word with the time it starts, in milliseconds."""

    start_ms: int
    text: str


@dataclass(frozen=True)
class Segment:
    """One line of captioned speech with the time it covers.

    ``words`` is empty when the format gives no word times. srv3 gives them;
    plain WebVTT does not.
    """

    start_ms: int
    end_ms: int
    text: str
    words: tuple[Word, ...] = ()


def _normalize(text: str) -> str:
    """Collapse whitespace and trim. Caption wording is left alone."""
    return _WHITESPACE.sub(" ", text).strip()


def _srv3_milliseconds(value: str | None, attribute: str, element: str) -> int:
    """Read a whole number of milliseconds out of an srv3 attribute."""
    if value is None:
        raise CaptionParseError(f"srv3 <{element}> has no {attribute} attribute")
    if not _WHOLE_NUMBER.match(value):
        raise CaptionParseError(
            f"srv3 <{element}> {attribute}={value!r} is not a whole number of milliseconds"
        )
    return int(value)


def parse_srv3(data: bytes) -> list[Segment]:
    """Parse an srv3 caption track into segments, in file order.

    Args:
        data: The bytes of the file, as downloaded.

    Returns:
        The segments that carry speech. An srv3 file whose body holds no
        ``<p>`` element yields an empty list.

    Raises:
        CaptionParseError: The bytes are not an srv3 (timedtext) document, or a
            ``<p>`` element that carries speech has no usable ``t`` or ``d``.
        TypeError: ``data`` is not bytes.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"parse_srv3 needs bytes, got {type(data).__name__}")
    if not data.strip():
        raise CaptionParseError("srv3 file is empty")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise CaptionParseError(f"srv3 file is not well-formed XML: {error}") from error

    if root.tag != "timedtext":
        raise CaptionParseError(
            f"srv3 file has root element <{root.tag}>, expected <timedtext>"
        )
    body = root.find("body")
    if body is None:
        raise CaptionParseError("srv3 <timedtext> has no <body> element")
    if sum(1 for _ in root.iter("p")) != sum(1 for _ in body.iter("p")):
        raise CaptionParseError("srv3 file has <p> elements outside its <body>")

    segments: list[Segment] = []
    for element in body.iter("p"):
        # <p> text can sit in <s> children or directly in the element, and both
        # kinds carry speech: ">> [snorts]" arrives as direct text.
        text = _normalize("".join(element.itertext()))
        if not text:
            # A line-append marker or an empty cue. It repeats no speech.
            continue
        start_ms = _srv3_milliseconds(element.get("t"), "t", "p")
        duration_ms = _srv3_milliseconds(element.get("d"), "d", "p")
        segments.append(
            Segment(
                start_ms=start_ms,
                end_ms=start_ms + duration_ms,
                text=text,
                words=_srv3_words(element, start_ms),
            )
        )
    return segments


def _srv3_words(element: ET.Element, segment_start_ms: int) -> tuple[Word, ...]:
    """Read word times from the ``<s>`` children of one srv3 ``<p>``.

    ``s@t`` is an offset in milliseconds from the ``<p>`` start. The format
    writes the first ``<s>`` without a ``t``, which means offset zero.
    """
    words: list[Word] = []
    for index, child in enumerate(element.findall("s")):
        text = _normalize(child.text or "")
        if not text:
            continue
        offset = child.get("t")
        if offset is None:
            if index != 0:
                raise CaptionParseError(
                    "srv3 <p> has an <s> element after the first with no t attribute"
                )
            word_start_ms = segment_start_ms
        else:
            word_start_ms = segment_start_ms + _srv3_milliseconds(offset, "t", "s")
        words.append(Word(start_ms=word_start_ms, text=text))
    return tuple(words)


def parse_vtt(data: bytes) -> list[Segment]:
    """Parse WebVTT captions into segments, in file order.

    Handles a ``WEBVTT`` header with metadata lines, ``NOTE``/``STYLE``/``REGION``
    blocks, cue identifiers, cue settings, the ``HH:MM:SS.mmm`` and ``MM:SS.mmm``
    timestamp forms, multi-line cues, and YouTube's inline word timing
    (``<00:00:01.234><c> word</c>``), which fills ``words``.

    Cues are kept in the order they are written, and multi-line cue text is
    joined with single spaces.

    Args:
        data: The bytes of the file. WebVTT is UTF-8, with or without a BOM.

    Returns:
        The segments that carry text. A file whose cues are all empty yields an
        empty list.

    Raises:
        CaptionParseError: The file has no ``WEBVTT`` header, holds a block with
            no timing line, holds a timestamp that cannot be read, holds a cue
            that ends before it starts, or is not UTF-8 text.
        TypeError: ``data`` is not bytes.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError(f"parse_vtt needs bytes, got {type(data).__name__}")
    if not data.strip():
        raise CaptionParseError("vtt file is empty")
    try:
        text = bytes(data).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CaptionParseError(f"vtt file is not UTF-8 text: {error}") from error

    lines = text.splitlines()
    if not lines or not lines[0].startswith("WEBVTT"):
        raise CaptionParseError("vtt file does not start with a WEBVTT line")

    segments: list[Segment] = []
    for index, block in enumerate(_vtt_blocks(lines[1:])):
        if block[0].startswith(_VTT_NON_CUE_BLOCKS):
            continue
        timing_at = next(
            (position for position, line in enumerate(block) if "-->" in line), None
        )
        if timing_at is None:
            if index == 0:
                # Header metadata lines, such as "Kind: captions" and
                # "Language: en", run up to the first blank line.
                continue
            raise CaptionParseError(
                f"vtt block has no timing line, starts with {block[0]!r}"
            )
        start_ms, end_ms = _vtt_cue_times(block[timing_at])
        if end_ms < start_ms:
            raise CaptionParseError(
                f"vtt cue ends before it starts: {block[timing_at]!r}"
            )
        cue_text, words = _vtt_cue_body(block[timing_at + 1 :])
        if not cue_text:
            continue
        segments.append(
            Segment(start_ms=start_ms, end_ms=end_ms, text=cue_text, words=words)
        )

    return segments


def _vtt_blocks(lines: list[str]) -> list[list[str]]:
    """Split cue lines into blocks at blank lines, dropping empty blocks."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _vtt_cue_times(timing_line: str) -> tuple[int, int]:
    """Read the start and end of a cue from its timing line."""
    match = _VTT_TIMING_LINE.match(timing_line.strip())
    if match is None:
        raise CaptionParseError(f"vtt cue timing line cannot be read: {timing_line!r}")
    return _vtt_time_to_ms(match.group(1)), _vtt_time_to_ms(match.group(2))


def _vtt_time_to_ms(timestamp: str) -> int:
    """Read ``HH:MM:SS.mmm`` or ``MM:SS.mmm`` as milliseconds."""
    match = _VTT_TIME.match(timestamp)
    if match is None:
        raise CaptionParseError(f"vtt timestamp cannot be read: {timestamp!r}")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    milliseconds = int(match.group(4))
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def _vtt_cue_body(payload: list[str]) -> tuple[str, tuple[Word, ...]]:
    """Read the text and the inline word times of one cue."""
    words: list[Word] = []
    for line in payload:
        parts = _INLINE_TIME.split(line)
        # parts is [text before the first time, time, text, time, text, ...].
        for index in range(1, len(parts) - 1, 2):
            word_text = _vtt_text(parts[index + 1])
            if word_text:
                words.append(
                    Word(start_ms=_vtt_time_to_ms(parts[index]), text=word_text)
                )
    cue_text = _normalize(" ".join(_vtt_text(line) for line in payload))
    return cue_text, tuple(words)


def _vtt_text(line: str) -> str:
    """Strip markup from one cue line and decode its character references."""
    return _normalize(_vtt_unescape(_TAG.sub("", line)))


def _vtt_unescape(text: str) -> str:
    """Decode the character references WebVTT defines, and numeric ones."""
    for entity, character in _VTT_ENTITIES.items():
        text = text.replace(entity, character)
    return _NUMERIC_ENTITY.sub(
        lambda match: chr(
            int(match.group(1)) if match.group(1) else int(match.group(2), 16)
        ),
        text,
    )
