"""Aligning an agenda with a transcript (spec 10.2).

The fixtures here are hand-made and small, one behaviour each, with one
exception: the segments of the reading of the council's rules are the captions
of the September 8, 2026 Longmont meeting (video 3qfQAkAAC9U) quoted verbatim,
because whether those words are mistaken for a transition is the difference
between an alignment a person can use and one that puts every item in the
wrong place. The same is true of the two sentences a member of the public
says at 483.0 s and 1776.2 s.

The published agenda times in these fixtures are the measured ones: the
clerk's ``data-videolocation`` on that agenda runs 466 s ahead of the video
clock, so an item reached at 0 s carries an agenda time of 466 s.
"""

from __future__ import annotations

from townrecord.adapters.base import AgendaItem
from townrecord.align import (
    HTML_VIDEO_TIMES,
    NO_ALIGNMENT,
    SPOKEN_TRANSITIONS,
    AlignmentResult,
    AlignmentSettings,
    align_agenda_with_transcript,
)

from .conftest import agenda_item, segment

#: The hand-written fixtures run on a video of exactly an hour.
HOUR_MS = 3_600_000

#: One fixture needs a video long enough to hold item 10's published time.
TWO_HOURS_MS = 7_200_000


def _fixture_item(
    number: str, title: str, video_seconds: int | None = None, *, video_s: int = 3600
) -> AgendaItem:
    """An item of a fixture video, carrying the status the adapter would give it."""
    return agenda_item(number, title, video_seconds, video_duration_s=video_s)


def _align(
    items: list[AgendaItem],
    transcript: tuple,
    *,
    duration_ms: int | None = HOUR_MS,
    **settings: object,
) -> AlignmentResult:
    """Align with the measured defaults, or with the numbers a test asks for."""
    return align_agenda_with_transcript(
        agenda_items=items,
        segments=transcript,
        video_duration_ms=duration_ms,
        settings=AlignmentSettings(**settings) if settings else None,
    )


def _item(result: AlignmentResult, number: str) -> object:
    """One item of the result, failing loudly when the number is not unique."""
    found = [item for item in result.items if item.number == number]
    assert len(found) == 1, f"{number} appears {len(found)} times in the result"
    return found[0]


def _three_anchors() -> tuple[list[AgendaItem], tuple]:
    """Three items the chair reached, each 466 s before its published time.

    Every one of them is named the way the transcript names items: after the
    word "item". Their titles are deliberately short in words over four
    letters, so nothing here rests on the title rule.
    """
    items = [
        _fixture_item("1.", "CALL TO ORDER", 466),
        _fixture_item("2.", "ROLL CALL", 566),
        _fixture_item("3.", "APPROVAL OF THE AGENDA", 666),
    ]
    transcript = (
        segment(0.0, "we are going to start with agenda item 1, the call to order."),
        segment(100.0, "we move to agenda item 2, roll call."),
        segment(200.0, "agenda item 3, approval of the agenda."),
    )
    return items, transcript


def test_the_measured_numbers_are_the_defaults() -> None:
    """Spec 10.2's offset is the one this video was measured with."""
    settings = AlignmentSettings()
    assert settings.offset_tolerance_s == 30.0
    assert settings.offset_min_anchors == 3


def test_three_agreeing_anchors_measure_one_offset() -> None:
    items, transcript = _three_anchors()
    result = _align(items, transcript)

    assert result.offset.accepted
    assert result.offset.offset_s == 466
    assert len(result.offset.anchors) == 3
    assert len(result.offset.agreeing) == 3
    assert "466 s ahead of the video" in result.offset.reason
    assert result.counts_by_method() == {
        SPOKEN_TRANSITIONS: 3,
        HTML_VIDEO_TIMES: 0,
        NO_ALIGNMENT: 0,
    }
    assert result.html_reason is None, "an accepted offset leaves no reason to report"


def test_the_offset_is_the_middle_of_the_agreeing_differences() -> None:
    """One anchor at the edge of the tolerance must not drag the offset with it.

    The four anchors differ by 466, 466, 466 and 496 s. All four are within the
    30 s tolerance of each other, so the middle of the four is 466 s and not
    the 481 s halfway between the extremes, and not the 473.5 s a mean of them
    would give.
    """
    items, transcript = _three_anchors()
    items = [*items, _fixture_item("4.", "SNOW REMOVAL CONTRACT", 962)]
    transcript = (*transcript, segment(466.0, "agenda item 4, snow removal contract."))
    result = _align(items, transcript)

    assert result.offset.offset_s == 466
    assert len(result.offset.agreeing) == 4
    assert "the middle of the agreeing differences is 466 s" in result.offset.reason


def test_an_agenda_time_shifted_by_the_offset_places_an_item() -> None:
    """Method 1: the published time, moved by the offset, where the transcript agrees."""
    items, transcript = _three_anchors()
    items = [*items, _fixture_item("4.", "AWARD OF CONTRACT FOR TRANSIT PROJECT", 3000)]
    transcript = (*transcript, segment(2534.0, "we will take up the transit item in a moment."))
    result = _align(items, transcript)

    item = _item(result, "4.")
    assert item.method == HTML_VIDEO_TIMES
    assert item.start_ms == 2_534_000, "3000 s published minus the 466 s offset"
    assert item.offset_s == 466
    assert item.agenda_seconds == 3000
    assert item.evidence == "the transcript near it mentions transit"
    assert item.end_ms == HOUR_MS, "the last placed item runs to the end of the video"


def test_a_placeholder_agenda_time_is_never_used() -> None:
    """Spec 10.2: a time outside the video is not a time.

    The placeholder on the real agenda is 585021 s on a 14407 s video. Here it
    is 585021 s on an hour-long video, and the item is refused as an agenda
    time rather than as a shifted one: the placeholder is never put through
    the offset at all, so it can never land in an answer by arithmetic.
    """
    items, transcript = _three_anchors()
    items = [*items, _fixture_item("5.", "MAYOR AND COUNCIL COMMENTS", 585021)]
    result = _align(items, transcript)

    item = _item(result, "5.")
    assert item.method == NO_ALIGNMENT
    assert item.start_ms is None
    assert item.offset_s is None
    assert "585021" in item.reason
    assert "falls outside the 3600 s video" in item.reason
    assert "shifted" not in item.reason
    assert result.counts_by_method() == {
        SPOKEN_TRANSITIONS: 3,
        HTML_VIDEO_TIMES: 0,
        NO_ALIGNMENT: 1,
    }


def test_a_two_anchor_agreement_is_not_enough_for_an_offset() -> None:
    """One agreement is a coincidence and two are a coincidence twice over."""
    items, transcript = _three_anchors()
    items = [
        items[0],
        items[1],
        _fixture_item("4.", "AWARD OF CONTRACT FOR TRANSIT PROJECT", 3000),
        _fixture_item("5.", "MAYOR AND COUNCIL COMMENTS", 585021),
    ]
    transcript = (*transcript, segment(2534.0, "we will take up the transit item in a moment."))
    result = _align(items, transcript)

    assert not result.offset.accepted
    assert result.offset.offset_s is None
    assert len(result.offset.agreeing) == 2
    assert "3 are needed" in result.offset.reason
    assert _item(result, "4.").method == NO_ALIGNMENT
    assert _item(result, "4.").start_ms is None, "no offset, so no published time is placed"
    assert "could not be shifted" in _item(result, "4.").reason
    assert result.counts_by_method() == {
        SPOKEN_TRANSITIONS: 2,
        HTML_VIDEO_TIMES: 0,
        NO_ALIGNMENT: 2,
    }
    assert result.html_reason == result.offset.reason


def test_the_anchor_threshold_is_what_refuses_two_anchors() -> None:
    """The two anchors really do agree; the count is the only thing against them.

    Item 4 is the item that shows it: nobody says "item 4" in the transcript,
    which only mentions the transit contract, so the published time moved by
    the offset is the only way it can be placed at all.
    """
    items, transcript = _three_anchors()
    items = [
        items[0],
        items[1],
        _fixture_item("4.", "AWARD OF CONTRACT FOR TRANSIT PROJECT", 3000),
    ]
    transcript = (*transcript, segment(2534.0, "we will take up the transit item in a moment."))
    result = _align(items, transcript, offset_min_anchors=2)

    assert result.offset.accepted
    assert result.offset.offset_s == 466
    assert len(result.offset.agreeing) == 2
    assert _item(result, "4.").method == HTML_VIDEO_TIMES
    assert _item(result, "4.").start_ms == 2_534_000


def test_anchors_further_apart_than_the_tolerance_do_not_agree() -> None:
    """The tolerance is what holds a video's anchors together, not the count.

    Three anchors differ by 466, 466 and 526 s, so only two of them are within
    30 s of each other and the offset is refused. The 466 s pair is the same
    pair that measures the offset in the other tests here, which is what makes
    the tolerance, rather than the evidence, the deciding thing.
    """
    items = [
        _fixture_item("1.", "CALL TO ORDER", 466),
        _fixture_item("2.", "ROLL CALL", 566),
        _fixture_item("3.", "APPROVAL OF THE AGENDA", 726),
    ]
    transcript = (
        segment(0.0, "we are going to start with agenda item 1, the call to order."),
        segment(100.0, "we move to agenda item 2, roll call."),
        segment(200.0, "agenda item 3, approval of the agenda."),
    )
    result = _align(items, transcript)

    assert not result.offset.accepted
    assert result.offset.offset_s is None
    assert len(result.offset.anchors) == 3
    assert len(result.offset.agreeing) == 2
    assert "only 2 of them land within 30 s of each other" in result.offset.reason
    # Nothing published is placed, and the two items the chair named keep the
    # times the transcript gave them.
    assert _item(result, "1.").method == SPOKEN_TRANSITIONS
    assert result.counts_by_method() == {
        SPOKEN_TRANSITIONS: 3,
        HTML_VIDEO_TIMES: 0,
        NO_ALIGNMENT: 0,
    }


def test_a_published_time_that_would_land_after_a_later_item_gives_way() -> None:
    """Words the chair said beat a time the clerk published.

    This is the measured case on the September 8, 2026 video: item 10's
    published time of 4440 s shifts to 3974.0 s, but the transcript had
    already reached 10.A at 3972.4 s, and the agenda places 10.A after item
    10. The published time is refused and item 10 keeps no boundary.
    """
    items = [
        _fixture_item("1.", "CALL TO ORDER", 466, video_s=7200),
        _fixture_item("2.", "ROLL CALL", 566, video_s=7200),
        _fixture_item("3.", "APPROVAL OF THE AGENDA", 666, video_s=7200),
        _fixture_item(
            "10.", "ORDINANCES ON SECOND READING AND PUBLIC HEARINGS", 4440, video_s=7200
        ),
        _fixture_item("10.A.", "FIRST AND MAIN TRANSIT PROJECT", 4447, video_s=7200),
    ]
    transcript = (
        segment(0.0, "we are going to start with agenda item 1, the call to order."),
        segment(100.0, "we move to agenda item 2, roll call."),
        segment(200.0, "agenda item 3, approval of the agenda."),
        # The measured captions, split across segments the way the captioner
        # split them, so no single segment holds item 10's whole title.
        segment(3967.52, "We are now on to items uh ordinances on"),
        segment(3970.64, "second reading and public hearings on"),
        segment(3972.40, "any matter. We have item A, first and"),
    )
    result = _align(items, transcript, duration_ms=TWO_HOURS_MS)

    assert _item(result, "10.A.").method == SPOKEN_TRANSITIONS
    assert _item(result, "10.A.").start_ms == 3_972_400
    ten = _item(result, "10.")
    assert ten.method == NO_ALIGNMENT
    assert ten.start_ms is None
    assert "10.A" in ten.reason
    assert "3972.4 s" in ten.reason
    assert "3974.0 s" in ten.reason
    assert result.item_for(4_000_000) == "10.A."


#: The measured captions of the chair reading the council's rules, 74 s to
#: 95 s of the September 8, 2026 video, quoted verbatim. Item titles are named
#: inside them ("first call public invited", "second reading", "the specific
#: item before the meeting") and not one of them is a transition to an item.
_RULES_READING = (
    segment(74.16, "speak during first call public invited"),
    segment(75.84, "to be heard. You must provide your"),
    segment(77.84, "address at the signup sheet before the"),
    segment(79.60, "meeting or I will not call your name."),
    segment(81.60, "Each speaker is limited to three"),
    segment(83.28, "minutes."),
    segment(86.32, "Anyone may speak on second reading or"),
    segment(88.80, "public hearing item and you are asked to"),
    segment(90.88, "add your name to the speaker list for"),
    segment(92.48, "the specific item before the meeting."),
    segment(94.64, "Anyone may speak during final call"),
)


def _consent_items() -> list[AgendaItem]:
    """The items of a consent agenda, none of them carrying a video time."""
    return [
        _fixture_item("1.", "CALL TO ORDER"),
        _fixture_item("2.", "FIRST CALL - PUBLIC INVITED TO BE HEARD"),
        _fixture_item("3.", "SECOND CALL - PUBLIC INVITED TO BE HEARD"),
        _fixture_item(
            "9.A.", "O-2026-57, A Bill For An Ordinance Making Additional Appropriations"
        ),
        _fixture_item("9.B.", "O-2026-58, A Bill For An Ordinance Amending Title 2"),
        _fixture_item("9.C.", "O-2026-59, A Bill For An Ordinance Directing City Staff"),
    ]


def _consent_transcript() -> tuple:
    """The measured captions around the reading of the rules and the ordinances."""
    return (
        segment(3.28, "I would now like to call the city of"),
        segment(5.28, "Longmont regular session September 8th,"),
        segment(9.52, "um regular session meeting to order. The"),
        *_RULES_READING,
        # A member of the public, during the first call, naming an item.
        segment(483.04, "going to ask that nine item 9B be"),
        # Another, citing the ordinance number, 1776.2 s into the meeting.
        segment(1776.16, "of 202658, the ordinance to establish a"),
        # The chair, reaching the consent agenda items for real.
        segment(3668.24, "September 22nd, 2026. Item 9A is"),
        segment(3671.52, "ordinance 2026-57,"),
        segment(3680.88, "2026. Item 9 B is ordinance 2026-58,"),
        segment(3690.40, "advisory board. Item 9 C is ordinance"),
    )


def test_a_reading_of_the_rules_is_not_a_transition_to_an_item() -> None:
    """The named check of this unit: 74 s to 95 s raises no boundary.

    The reading names two item titles ("public invited to be heard" appears
    inside it, split across segments) and three number words, and none of it
    is the chair moving to an item.
    """
    result = _align(_consent_items(), _consent_transcript())

    inside = [item for item in result.aligned_items() if 74_000 <= item.start_ms < 95_000]
    assert inside == [], "the reading of the rules is not a boundary"
    assert _item(result, "2.").method == NO_ALIGNMENT
    assert _item(result, "2.").start_ms is None
    assert "no spoken transition matched" in _item(result, "2.").reason
    assert _item(result, "3.").start_ms is None


def test_the_items_the_transcript_really_reaches_are_taken_in_order() -> None:
    """The chair's own transitions, in agenda order, and no other boundaries."""
    result = _align(_consent_items(), _consent_transcript())

    assert _item(result, "9.A.").start_ms == 3_668_240
    assert _item(result, "9.A.").evidence == "Item 9A is"
    assert _item(result, "9.B.").start_ms == 3_680_880
    assert _item(result, "9.C.").start_ms == 3_690_400
    assert result.counts_by_method() == {
        SPOKEN_TRANSITIONS: 3,
        HTML_VIDEO_TIMES: 0,
        NO_ALIGNMENT: 3,
    }
    assert result.spoken_reason is None


def test_a_public_speaker_naming_an_item_is_not_its_transition() -> None:
    """Two measured mentions outside the chair's order, both passed over.

    At 483.0 s a speaker asks that item 9B be removed, and at 1776.2 s another
    cites ordinance 2026-58. Neither is the meeting reaching that item, which
    happened at 3680.9 s. Both are reported as dropped, with the reading that
    was kept instead, so the answer is traceable rather than silent.
    """
    result = _align(_consent_items(), _consent_transcript())

    assert result.item_for(483_040) is None
    assert result.item_for(1_776_160) is None
    dropped = result.dropped
    assert any(
        line.startswith("483.0 s") and "9.B" in line and "3680.9 s" in line for line in dropped
    ), dropped
    assert any(line.startswith("1776.2 s") and "9.B" in line for line in dropped), dropped
    # The same item was read twice, 3668.24 s and 3671.52 s; the first is kept.
    assert any(
        line.startswith("3671.5 s") and "9.A" in line and "3668.2 s" in line for line in dropped
    ), dropped


def test_without_a_transcript_nothing_is_placed_and_the_reason_says_so() -> None:
    items = [
        _fixture_item("1.", "CALL TO ORDER", 466),
        _fixture_item("2.", "ROLL CALL", 566),
    ]
    result = _align(items, ())

    assert result.spoken_reason == "the meeting has no transcript to align with"
    assert not result.offset.accepted
    assert result.offset.reason == (
        "no spoken transition named an item that also carries an agenda time, "
        "so no offset could be measured"
    )
    assert result.html_reason == result.offset.reason
    assert result.counts_by_method() == {
        SPOKEN_TRANSITIONS: 0,
        HTML_VIDEO_TIMES: 0,
        NO_ALIGNMENT: 2,
    }
    for item in result.items:
        assert item.start_ms is None
        assert item.reason is not None
        assert "no spoken transition matched" in item.reason


def test_without_the_video_length_no_published_time_can_be_placed() -> None:
    """Without a length, no agenda time can be checked against the video."""
    items = [
        _fixture_item("1.", "CALL TO ORDER", 466),
        _fixture_item("2.", "ROLL CALL", 566),
    ]
    transcript = (segment(0.0, "agenda item 1, the call to order."),)
    result = _align(items, transcript, duration_ms=None)

    assert result.video_duration_ms is None
    assert result.html_reason == (
        "the video's length is not known, so no agenda time could be placed"
    )
    # The transcript's own end is the last thing known about the video.
    assert "falls outside the 2 s video" in _item(result, "2.").reason


def test_an_item_with_no_published_time_says_that_is_what_is_missing() -> None:
    items = [
        _fixture_item("1.", "CALL TO ORDER"),
        _fixture_item("2.", "ROLL CALL", 566),
    ]
    result = _align(items, (segment(0.0, "we move to agenda item 2, roll call."),))

    item = _item(result, "1.")
    assert item.method == NO_ALIGNMENT
    assert "the agenda carries no video time for it" in item.reason.lower()


def test_the_last_item_ends_at_the_end_of_the_video() -> None:
    items, transcript = _three_anchors()
    result = _align(items, transcript)

    assert _item(result, "1.").end_ms == _item(result, "2.").start_ms
    assert _item(result, "2.").end_ms == _item(result, "3.").start_ms
    assert _item(result, "3.").end_ms == HOUR_MS


def test_every_boundary_answers_for_its_own_item() -> None:
    """The item of a moment is kept in one place: the boundaries themselves."""
    items, transcript = _three_anchors()
    result = _align(items, transcript)

    for item in result.aligned_items():
        assert result.item_for(item.start_ms) == item.number
    assert result.item_for(0) == "1."
    assert result.item_for(99_999) == "1."
    assert result.item_for(100_000) == "2."
    # The last item runs to the end of the video, so a moment long after its
    # boundary still belongs to it rather than to nothing.
    assert result.item_for(3_000_000) == "3."
    # Half open: the moment the video ends belongs to no item, and neither
    # does a moment before the video began.
    assert result.item_for(HOUR_MS) is None
    assert result.item_for(-1) is None


def test_aligned_and_unaligned_items_together_are_the_agenda() -> None:
    items = [
        _fixture_item("1.", "CALL TO ORDER", 466),
        _fixture_item("2.", "ROLL CALL"),
        _fixture_item("3.", "APPROVAL OF THE AGENDA", 666),
    ]
    transcript = (segment(0.0, "agenda item 1, the call to order."),)
    result = _align(items, transcript)

    assert [item.number for item in result.aligned_items()] == ["1."]
    assert [item.number for item in result.unaligned_items()] == ["2.", "3."]
    assert len(result.aligned_items()) + len(result.unaligned_items()) == len(result.items) == 3
