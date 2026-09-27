"""Unit tests for the srv3 and VTT caption parsers.

Fixtures under ``fixtures/`` are either hand written or a short excerpt of a real
yt-dlp download (see the D1 unit report). The real download itself is too large to
copy into the repo, so the last test reads it from the evidence folder and skips
with a clear message when it is not there.
"""

from __future__ import annotations

import pathlib

import pytest

from townrecord.captions import CaptionParseError, Segment, Word, parse_srv3, parse_vtt

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
EXCERPT_SRV3 = FIXTURES / "excerpt.srv3"
DIRECT_TEXT_SRV3 = FIXTURES / "direct_text.srv3"
YOUTUBE_VTT = FIXTURES / "youtube.en.vtt"

REAL_SRV3 = pathlib.Path(
    r"C:\Users\scott\Desktop\Code\townrecord-oversight\evidence\fixtures\youtube"
    r"\captions\3qfQAkAAC9U.en.srv3"
)

# Measured on the real file on 2026-09-27 with the parser in this unit:
# 11573 <p> elements, 5787 of them not empty after trimming. 53 of those 5787 are
# direct text in the <p> element ("[snorts]"), so they carry no word times.
REAL_SRV3_P_ELEMENTS = 11573
REAL_SRV3_SEGMENTS = 5787
REAL_SRV3_SEGMENTS_WITH_WORDS = 5734
REAL_SRV3_WORDS = 36730


def read_bytes(path: pathlib.Path) -> bytes:
    return path.read_bytes()


# --- srv3 -----------------------------------------------------------------


def test_srv3_excerpt_segment_count() -> None:
    segments = parse_srv3(read_bytes(EXCERPT_SRV3))
    # The excerpt holds 20 <p> elements. 10 carry speech, 10 are line-append markers.
    assert len(segments) == 10


def test_srv3_excerpt_first_segment() -> None:
    first = parse_srv3(read_bytes(EXCERPT_SRV3))[0]
    assert first.start_ms == 3280
    assert first.end_ms == 3280 + 4559
    assert first.text.startswith("I would now like to call the city of")


def test_srv3_excerpt_keeps_speech_verbatim() -> None:
    segments = parse_srv3(read_bytes(EXCERPT_SRV3))
    texts = [segment.text for segment in segments]
    # 10.1 and 10.6 read the raw caption text, so nothing is corrected or dropped.
    assert "um regular session meeting to order. The" in texts
    assert ">> Mary Dogaring," in texts


def test_srv3_word_times_are_offsets_from_the_segment_start() -> None:
    first = parse_srv3(read_bytes(EXCERPT_SRV3))[0]
    assert first.words[0] == Word(start_ms=3280, text="I")
    # The first <s> has no t attribute, so it sits at the segment start.
    assert first.words[1] == Word(start_ms=3280 + 320, text="would")
    assert first.words[-1].text == "of"
    assert [word.text for word in first.words][:4] == ["I", "would", "now", "like"]


def test_srv3_segments_are_ordered_and_never_empty() -> None:
    segments = parse_srv3(read_bytes(EXCERPT_SRV3))
    assert all(segment.text for segment in segments)
    assert all(segment.text == segment.text.strip() for segment in segments)
    starts = [segment.start_ms for segment in segments]
    assert starts == sorted(starts)
    assert all(segment.end_ms >= segment.start_ms for segment in segments)


def test_srv3_decodes_entities() -> None:
    segments = parse_srv3(read_bytes(EXCERPT_SRV3))
    texts = [segment.text for segment in segments]
    assert "viewed uh on the city's YouTube channel" in texts
    assert not any("&#39;" in text or "&amp;" in text for text in texts)


def test_srv3_direct_text_in_p_element_is_kept() -> None:
    segments = parse_srv3(read_bytes(DIRECT_TEXT_SRV3))
    assert [segment.text for segment in segments] == [
        "Roll call, please.",
        ">> [snorts and clears throat]",
        "The city's budget & more",
    ]
    # A <p> with no <s> children gives no word times.
    assert segments[1].words == ()
    assert segments[1].start_ms == 4500
    assert segments[1].end_ms == 4500 + 2020
    assert [word.text for word in segments[2].words] == [
        "The",
        "city's",
        "budget & more",
    ]


def test_srv3_rejects_a_file_that_is_not_xml() -> None:
    with pytest.raises(CaptionParseError):
        parse_srv3(b"this is not xml at all")


def test_srv3_rejects_another_xml_document() -> None:
    with pytest.raises(CaptionParseError):
        parse_srv3(b"<html><body><p>hello</p></body></html>")


def test_srv3_rejects_a_timedtext_without_a_body() -> None:
    with pytest.raises(CaptionParseError):
        parse_srv3(b'<timedtext format="3"><head/></timedtext>')


def test_srv3_empty_body_gives_no_segments() -> None:
    assert parse_srv3(b'<timedtext format="3"><body></body></timedtext>') == []


def test_srv3_rejects_a_segment_without_a_duration() -> None:
    with pytest.raises(CaptionParseError):
        parse_srv3(
            b'<timedtext format="3"><body><p t="10"><s>hi</s></p></body></timedtext>'
        )


def test_srv3_rejects_a_segment_without_a_start() -> None:
    with pytest.raises(CaptionParseError):
        parse_srv3(
            b'<timedtext format="3"><body><p d="10"><s>hi</s></p></body></timedtext>'
        )


def test_srv3_rejects_a_time_that_is_not_a_number() -> None:
    with pytest.raises(CaptionParseError):
        parse_srv3(
            b'<timedtext format="3"><body><p t="10" d="soon"><s>hi</s></p></body></timedtext>'
        )


def test_srv3_rejects_a_negative_duration() -> None:
    with pytest.raises(CaptionParseError):
        parse_srv3(
            b'<timedtext format="3"><body><p t="10" d="-5"><s>hi</s></p></body></timedtext>'
        )


def test_srv3_rejects_a_later_word_without_a_time() -> None:
    # Only the first <s> of a <p> may omit t. Later ones would need a guessed time.
    with pytest.raises(CaptionParseError):
        parse_srv3(
            b'<timedtext format="3"><body><p t="0" d="10"><s>a</s><s>b</s></p></body></timedtext>'
        )


def test_srv3_rejects_segments_outside_the_body() -> None:
    with pytest.raises(CaptionParseError):
        parse_srv3(
            b'<timedtext format="3"><p t="0" d="10"><s>a</s></p><body/></timedtext>'
        )


# --- VTT ------------------------------------------------------------------


def test_vtt_cue_with_an_identifier() -> None:
    segments = parse_vtt(read_bytes(YOUTUBE_VTT))
    assert segments[0] == Segment(
        start_ms=3280,
        end_ms=7839,
        text="I would now like to call the city of",
        words=(),
    )


def test_vtt_multiline_cue_becomes_one_segment() -> None:
    segments = parse_vtt(read_bytes(YOUTUBE_VTT))
    assert segments[1].start_ms == 7839
    assert segments[1].end_ms == 12089
    assert segments[1].text == "Longmont regular session September 8th, 2026"


def test_vtt_accepts_the_short_timestamp_form_and_decodes_entities() -> None:
    by_start = {
        segment.start_ms: segment for segment in parse_vtt(read_bytes(YOUTUBE_VTT))
    }
    short_form = by_start[62500]
    assert short_form.end_ms == ((1 * 60 * 60) + (2 * 60) + 3) * 1000 + 4
    assert short_form.text == "Tom's & Jerry"


def test_vtt_reads_inline_word_times() -> None:
    by_start = {
        segment.start_ms: segment for segment in parse_vtt(read_bytes(YOUTUBE_VTT))
    }
    last = by_start[30000]
    assert last.text == "Hello council members"
    assert last.words == (
        Word(start_ms=30000, text="Hello"),
        Word(start_ms=31234, text="council"),
        Word(start_ms=32500, text="members"),
    )


def test_vtt_drops_empty_cues_and_skips_notes() -> None:
    segments = parse_vtt(read_bytes(YOUTUBE_VTT))
    # Five cues in the fixture: the NOTE block is not a cue and the cue with no
    # text is dropped, so four segments remain.
    assert len(segments) == 4
    assert all(segment.text for segment in segments)
    starts = [segment.start_ms for segment in segments]
    assert starts == sorted(starts)


def test_vtt_without_any_text_gives_no_segments() -> None:
    data = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n\n"
    assert parse_vtt(data) == []


def test_vtt_rejects_a_file_without_a_header() -> None:
    with pytest.raises(CaptionParseError):
        parse_vtt(b"1\n00:00:01.000 --> 00:00:02.000\nhello\n")


def test_vtt_rejects_an_empty_file() -> None:
    with pytest.raises(CaptionParseError):
        parse_vtt(b"")


def test_vtt_skips_header_metadata_lines() -> None:
    data = b"WEBVTT\nKind: captions\nLanguage: en\n\n00:00:01.000 --> 00:00:02.000\nhello\n"
    segments = parse_vtt(data)
    assert [segment.text for segment in segments] == ["hello"]


def test_vtt_rejects_a_block_without_a_timing_line() -> None:
    data = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhello\n\nthis block has no cue\n"
    with pytest.raises(CaptionParseError):
        parse_vtt(data)


def test_vtt_rejects_a_bad_timestamp() -> None:
    with pytest.raises(CaptionParseError):
        parse_vtt(b"WEBVTT\n\n00:00:01,000 --> 00:00:02.000\nhello\n")


def test_vtt_rejects_a_cue_that_ends_before_it_starts() -> None:
    with pytest.raises(CaptionParseError):
        parse_vtt(b"WEBVTT\n\n00:00:09.000 --> 00:00:02.000\nhello\n")


def test_vtt_rejects_bytes_that_are_not_text() -> None:
    with pytest.raises(CaptionParseError):
        parse_vtt(b"WEBVTT\n\n\xff\xfe\x00\x00 --> 00:00:02.000\nhello\n")


def test_parsers_require_bytes() -> None:
    with pytest.raises(TypeError):
        parse_srv3("<timedtext/>")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_vtt("WEBVTT")  # type: ignore[arg-type]


# --- the real download ----------------------------------------------------


@pytest.mark.skipif(
    not REAL_SRV3.exists(),
    reason=f"real srv3 fixture not present at {REAL_SRV3}",
)
def test_real_longmont_srv3() -> None:
    segments = parse_srv3(REAL_SRV3.read_bytes())
    assert len(segments) == REAL_SRV3_SEGMENTS
    assert all(segment.text for segment in segments)
    starts = [segment.start_ms for segment in segments]
    assert starts == sorted(starts)
    assert all(segment.end_ms >= segment.start_ms for segment in segments)
    assert segments[0].start_ms == 3280
    assert segments[0].text.startswith("I would now like to call the city of")
    with_words = [segment for segment in segments if segment.words]
    assert len(with_words) == REAL_SRV3_SEGMENTS_WITH_WORDS
    assert sum(len(segment.words) for segment in segments) == REAL_SRV3_WORDS
    assert all(segment.words[0].start_ms >= segment.start_ms for segment in with_words)
    assert all(segment.words[-1].start_ms <= segment.end_ms for segment in with_words)
