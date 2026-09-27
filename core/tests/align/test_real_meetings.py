"""Aligning the two recorded Longmont meetings (spec 10.1, 10.2; spec 18's count).

These tests read the recorded evidence in place, from
``townrecord-oversight/evidence/fixtures``, and are skipped when it is not on
this machine. Nothing is copied into the repository: the srv3 caption track is
a multi-megabyte recording of a real meeting.

The first of the two meetings is the one this unit was measured against, the
September 8, 2026 City Council regular session (agenda 16805, video
3qfQAkAAC9U, 14407 s). Its clerk-published agenda times run 466 s ahead of the
video clock, and the alignment is expected to find that out for itself. The
second is the June 2, 2026 study session (agenda 16095), whose HTML carries no
``data-videolocation`` at all and which has no recorded transcript here, so
neither method can place anything and both have to say why.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from townrecord.adapters.base import AgendaItem
from townrecord.adapters.primegov import PrimeGovAdapter
from townrecord.align import (
    HTML_VIDEO_TIMES,
    NO_ALIGNMENT,
    SPOKEN_TRANSITIONS,
    AlignmentResult,
    align_agenda_with_transcript,
)
from townrecord.captions.parse import parse_srv3

from .conftest import (
    AGENDA_16095,
    AGENDA_16805,
    CAPTIONS_16805,
    MEASURED_MIN_ANCHORS,
    MEASURED_TOLERANCE_S,
    VIDEO_DURATION_S,
    needs,
)

#: The adapter is never asked to make a request here; the agenda is a file.
BASE_URL = "https://portal.test.invalid"

#: What the September 8, 2026 meeting was measured to align as, item by item.
MEASURED_COUNTS = {
    SPOKEN_TRANSITIONS: 22,
    HTML_VIDEO_TIMES: 6,
    NO_ALIGNMENT: 12,
}

#: The offset the clerk's times run ahead of the video clock, in seconds.
MEASURED_OFFSET_S = 466

#: The items that stay unplaced, by the numbers the agenda gives them. The
#: last two are the appendix entries, numbered "1" and "2" with no dot, which
#: is how the agenda numbers them and how they are reported.
MEASURED_UNALIGNED = {"2.", "4.", "6.", "7.", "9.", "9.I", "10.", "15.", "16.", "17.", "1", "2"}

#: The reading of the council's rules, 74 s to 95 s. Nothing may start there.
RULES_READING_MS = (74_000, 95_000)


def _adapter() -> PrimeGovAdapter:
    """The PrimeGov adapter, with the proxy-blind client this machine needs."""
    return PrimeGovAdapter(BASE_URL, httpx.Client(trust_env=False))


def _agenda_items(path: Path) -> list[AgendaItem]:
    """The agenda the adapter reads out of a recorded HTML page."""
    return _adapter().parse_html_agenda(path.read_bytes(), VIDEO_DURATION_S)


@pytest.fixture(scope="module")
def september() -> AlignmentResult:
    """The measured alignment of the September 8, 2026 regular session."""
    items = _agenda_items(AGENDA_16805)
    segments = parse_srv3(CAPTIONS_16805.read_bytes())
    return align_agenda_with_transcript(
        agenda_items=items,
        segments=segments,
        video_duration_ms=VIDEO_DURATION_S * 1000,
    )


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_september_meeting_measures_its_own_offset(september: AlignmentResult) -> None:
    """Method 1's offset is measured from this video, not assumed."""
    offset = september.offset
    assert offset.accepted
    assert offset.offset_s == MEASURED_OFFSET_S
    assert offset.tolerance_s == MEASURED_TOLERANCE_S
    assert offset.min_anchors == MEASURED_MIN_ANCHORS
    assert len(offset.anchors) == 22, "every item named in speech and on the agenda is an anchor"
    assert len(offset.agreeing) == 10
    assert f"{MEASURED_OFFSET_S} s ahead of the video" in offset.reason
    for anchor in offset.agreeing:
        assert abs(anchor.difference_s - MEASURED_OFFSET_S) <= MEASURED_TOLERANCE_S
        assert anchor.matched_text, "an anchor records the words that made it"


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_september_meeting_aligns_by_all_three_methods(september: AlignmentResult) -> None:
    """Spec 18's count, for the one meeting where all three methods apply."""
    assert len(september.items) == 40
    assert september.counts_by_method() == MEASURED_COUNTS
    assert september.spoken_reason is None
    assert september.html_reason is None


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_september_meeting_puts_the_measured_items_where_they_were_said(
    september: AlignmentResult,
) -> None:
    """The places a person can check by watching the recording."""
    first_ordinance = next(item for item in september.items if item.number == "10.A.1")
    assert first_ordinance.method == SPOKEN_TRANSITIONS
    assert first_ordinance.start_ms == 3_978_160
    assert first_ordinance.evidence is not None
    assert "2026-54" in first_ordinance.evidence

    consent = next(item for item in september.items if item.number == "9.A")
    assert consent.start_ms == 3_668_240

    # Method 1's own placements, moved by the measured offset.
    council_comments = next(item for item in september.items if item.number == "13.")
    assert council_comments.method == HTML_VIDEO_TIMES
    assert council_comments.offset_s == MEASURED_OFFSET_S
    assert council_comments.agenda_seconds == 14_105
    assert council_comments.start_ms == 13_639_000


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_september_meeting_never_takes_the_rules_reading_for_a_transition(
    september: AlignmentResult,
) -> None:
    """The measured trap: 74 s to 95 s names items and moves to none of them.

    The chair reads the rules for public comment there, naming "first call",
    "public invited to be heard" and "second reading" — words that are also
    item titles. Nothing may be placed by them. The moment 74.5 s belongs to
    whatever item was already under way when the reading began, and that item
    started before the reading did.
    """
    start, end = RULES_READING_MS
    inside = [item for item in september.aligned_items() if start <= item.start_ms < end]
    assert inside == [], "no item may be placed by the reading of the rules"
    for moment in (74_500, 80_000, 90_000, 94_000):
        number = september.item_for(moment)
        assert number is not None
        covering = next(item for item in september.items if item.number == number)
        assert covering.start_ms < start, f"{moment} ms was started by the reading itself"


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_september_meeting_reports_the_readings_it_passed_over(
    september: AlignmentResult,
) -> None:
    """A reading that was passed over is reported, not silently dropped.

    At 6104.8 s someone says "agenda item B that", which the identifier rules
    read as item 12.B. The chair reached 12.B at 10313.0 s, and both facts are
    in the answer, so a person can see why the earlier one lost.
    """
    assert september.dropped, "the dropped readings are reported"
    assert any(
        line.startswith("6104.8 s")
        and "12.B" in line
        and "agenda item B that" in line
        and "10313.0 s" in line
        for line in september.dropped
    ), september.dropped


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_item_of_a_moment_is_answered_by_the_boundaries(september: AlignmentResult) -> None:
    """Spec 10.2: the item of a segment is kept in one place, and asked for here."""
    assert september.item_for(219_000) == "8."
    assert september.item_for(1_776_160) == "8.", "a public speaker is not a boundary"
    assert september.item_for(3_978_160) == "10.A.1"
    assert september.item_for(4_657_920) == "11."
    assert september.item_for(10_313_040) == "12.B"
    assert september.item_for(13_641_040) == "13."
    for item in september.aligned_items():
        assert september.item_for(item.start_ms) == item.number


@needs(AGENDA_16805, CAPTIONS_16805)
def test_the_september_meeting_says_why_each_unplaced_item_was_unplaced(
    september: AlignmentResult,
) -> None:
    """An unplaced item carries a reason, and the reason is one of the known ones."""
    unplaced = september.unaligned_items()
    assert {item.number for item in unplaced} == MEASURED_UNALIGNED
    for item in unplaced:
        assert item.method == NO_ALIGNMENT
        assert item.start_ms is None
        assert item.reason, f"{item.number} was left unplaced without a reason"
        assert "no spoken transition matched" in item.reason, item.reason

    reasons = {item.number: item.reason.lower() for item in unplaced}
    # No published time at all: the six items of the agenda proper, and the two
    # appendix entries the agenda numbers without a dot.
    for number in ("2.", "4.", "6.", "7.", "15.", "16.", "1", "2"):
        assert "the agenda carries no video time for it" in reasons[number], reasons[number]
    # Two placeholder times on this agenda, both longer than the video, and
    # the last item's real time, which is past the end of the recording.
    assert "585015" in reasons["9."] and "outside the 14407 s video" in reasons["9."]
    assert "585021" in reasons["9.I"] and "outside the 14407 s video" in reasons["9.I"]
    assert "14864" in reasons["17."]
    # Item 10's published time gives way to the spoken transition at 10.A.
    assert "10.a" in reasons["10."] and "3972.4 s" in reasons["10."]


@needs(AGENDA_16095)
def test_the_study_session_without_times_or_a_transcript_places_nothing() -> None:
    """The second meeting: every method gives nothing, and each says why.

    Agenda 16095 has no ``data-videolocation`` attribute at all, and no
    caption track is recorded for it, so method 1 has no anchor to measure an
    offset from, method 2 has nothing to read, and method 3's reason is the
    one every item carries.
    """
    items = _agenda_items(AGENDA_16095)
    assert len(items) == 14
    assert all(item.video_seconds is None for item in items)

    result = align_agenda_with_transcript(
        agenda_items=items,
        segments=(),
        video_duration_ms=VIDEO_DURATION_S * 1000,
    )

    assert result.counts_by_method() == {
        SPOKEN_TRANSITIONS: 0,
        HTML_VIDEO_TIMES: 0,
        NO_ALIGNMENT: 14,
    }
    assert not result.offset.accepted
    assert result.offset.offset_s is None
    assert result.offset.anchors == ()
    assert result.offset.reason == (
        "no spoken transition named an item that also carries an agenda time, "
        "so no offset could be measured"
    )
    assert result.spoken_reason == "the meeting has no transcript to align with"
    assert result.html_reason == result.offset.reason
    for item in result.items:
        assert "the agenda carries no video time for it" in item.reason.lower()
    assert result.item_for(0) is None, "nothing was placed, so no moment has an item"
