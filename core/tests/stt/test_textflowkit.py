"""Transcribing locally: the command, the limit, and the JSON (spec 8.5, 8.10).

The transcriber is not installed and no model is downloaded. What is tested is
what this service hands the tool and what it does with what comes back.
"""

from __future__ import annotations

import pytest

from townrecord.captions import CaptionParseError, Segment, Word
from townrecord.stt import (
    parse_textflowkit_json,
    textflowkit_argv,
    transcription_timeout_s,
)

from .conftest import segment, transcript_bytes, word


def test_the_transcription_command_is_the_spec_command() -> None:
    """Spec 8.5's command, argument for argument."""
    assert textflowkit_argv("capture/v-0001/meeting.opus", "capture/v-0001/transcript") == [
        "textflowkit",
        "transcribe",
        "capture/v-0001/meeting.opus",
        "--formats",
        "json",
        "--output-dir",
        "capture/v-0001/transcript",
        "--model",
        "small",
        "--language",
        "en",
    ]


def test_the_model_and_language_are_named_in_the_command() -> None:
    """A caller can ask for a different model and language, and both are read."""
    argv = textflowkit_argv("a.opus", "out", model="medium", language="es")
    assert argv[argv.index("--model") + 1] == "medium"
    assert argv[argv.index("--language") + 1] == "es"


@pytest.mark.parametrize(
    ("audio_seconds", "expected"),
    [
        (0.0, 600.0),
        (790.0, 1785.0),
        (3600.0, 6000.0),
    ],
)
def test_the_time_limit_is_the_recording_times_one_and_a_half_plus_a_floor(
    audio_seconds: float, expected: float
) -> None:
    """A recording of no length at all still gets the floor."""
    assert transcription_timeout_s(audio_seconds) == expected


def test_a_recording_of_unknown_length_gets_an_hour() -> None:
    """The download did not report a length, so the limit is the flat one."""
    assert transcription_timeout_s(None) == 3600.0


def test_a_negative_length_is_refused() -> None:
    """No recording has one, so it is a caller's mistake and not a limit."""
    with pytest.raises(ValueError):
        transcription_timeout_s(-1.0)


@pytest.mark.parametrize(
    ("seconds", "milliseconds"),
    [
        (0.0, 0),
        (0.5, 500),
        (2.25, 2250),
        (3.0005, 3001),
        (0.0025, 3),
        (99.9999, 100000),
    ],
)
def test_seconds_become_whole_milliseconds_rounded_half_up(
    seconds: float, milliseconds: int
) -> None:
    """A tie goes up, on any Python.

    ``3.0005`` seconds and ``0.0025`` seconds are the ties worth watching:
    Python's built-in ``round`` sends both to the even millisecond (3000 and
    2), which would make the stored time depend on the number rather than on
    the recording.
    """
    data = transcript_bytes([segment(seconds, seconds + 1.0, "a word")])
    assert parse_textflowkit_json(data)[0].start_ms == milliseconds


def test_a_hidden_segment_is_left_out_of_the_transcript() -> None:
    """TextFlowKit keeps a hidden segment out of its own text and exports.

    Nothing in its transcription pipeline sets the mark, and neither the
    shared segment type nor the ``segments`` table has a place to keep it, so
    a hidden segment does not become stored speech.
    """
    data = transcript_bytes(
        [
            segment(0.0, 1.0, "Good evening."),
            segment(1.0, 2.0, "and this line is not to be kept.", hidden=True),
            segment(2.0, 3.0, "We are called to order."),
        ]
    )
    assert [item.text for item in parse_textflowkit_json(data)] == [
        "Good evening.",
        "We are called to order.",
    ]


def test_a_speaker_label_is_kept_as_written_and_never_invented() -> None:
    """The label is the transcriber's. None means it named no speaker."""
    data = transcript_bytes(
        [
            segment(0.0, 1.0, "Good evening.", speaker="SPEAKER_00"),
            segment(1.0, 2.0, "We are called to order."),
            segment(2.0, 3.0, "All in favor.", speaker="  "),
            segment(3.0, 4.0, "Motion carries.", speaker=" Mayor Peck "),
        ]
    )
    assert [item.speaker for item in parse_textflowkit_json(data)] == [
        "SPEAKER_00",
        None,
        None,
        "Mayor Peck",
    ]


def test_a_segment_is_the_type_the_caption_parser_returns() -> None:
    """One segment type for captions and for local speech (spec 10.5)."""
    data = transcript_bytes(
        [
            segment(
                3.0,
                5.5,
                "agenda item 1, the call to order.",
                speaker="SPEAKER_00",
                words=[word(3.0, 3.4, "agenda"), word(3.4, 3.9, "item")],
            )
        ]
    )
    (segment_result,) = parse_textflowkit_json(data)
    assert segment_result == Segment(
        start_ms=3000,
        end_ms=5500,
        text="agenda item 1, the call to order.",
        words=(Word(start_ms=3000, text="agenda", end_ms=3400), Word(3400, "item", 3900)),
        speaker="SPEAKER_00",
    )


def test_a_segment_with_no_words_has_none() -> None:
    """Plain output without word times is not an error (srv3 gives them, VTT does not)."""
    (read,) = parse_textflowkit_json(transcript_bytes([segment(0.0, 1.0, "Good evening.")]))
    assert read.words == ()


def test_a_transcript_with_no_segments_is_empty() -> None:
    assert parse_textflowkit_json(transcript_bytes([])) == []


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not json at all",
        b"[1, 2, 3]",
        b'{"segments": {"0": {}}}',
        b'{"segments": ["a segment"]}',
        b'{"segments": [{"start": 0.0, "end": 1.0, "text": 5}]}',
        b'{"segments": [{"start": 0.0, "end": 1.0}]}',
        b'{"segments": [{"start": "0", "end": 1.0, "text": "a word"}]}',
        b'{"segments": [{"start": 0.0, "end": 1.0, "text": "a word", "words": {}}]}',
        b'{"segments": [{"start": -1.0, "end": 1.0, "text": "a word"}]}',
    ],
)
def test_a_file_that_is_not_a_textflowkit_transcript_is_refused(data: bytes) -> None:
    """One error type, the caption parser's own, and no partly read list."""
    with pytest.raises(CaptionParseError):
        parse_textflowkit_json(data)


def test_the_file_must_be_bytes() -> None:
    """Same rule as the caption parser: text is not a file."""
    with pytest.raises(TypeError):
        parse_textflowkit_json('{"segments": []}')  # type: ignore[arg-type]
