"""Reading identifiers out of agenda titles and out of speech (spec 10.1).

Every spoken form in :data:`MEASURED_SPOKEN` was copied out of the September 8,
2026 Longmont transcript (video 3qfQAkAAC9U) or out of spec 10.1 itself. They
are the reason this module exists: the clerk writes ``O-2026-54`` and the
captioner writes whatever was said.
"""

from __future__ import annotations

import pytest

from townrecord.align.identifiers import (
    NUMBER_WORDS,
    ORDINANCE,
    RESOLUTION,
    find_identifiers,
    find_item_references,
    significant_words,
    words_of,
)

#: (caption text, the agenda number it names, the kind the text gives it).
#: The first four are measured on the September 8, 2026 transcript, the last
#: two come from spec 10.1's own examples.
MEASURED_SPOKEN = [
    ("ordinance 2026-54,", (2026, 54), ORDINANCE),
    ("ordinance 202654.", (2026, 54), ORDINANCE),
    ("with item A1, ordinance 2026-54,", (2026, 54), ORDINANCE),
    ("2026-56,", (2026, 56), None),
    ("ordinance um 2026-58", (2026, 58), ORDINANCE),
    ("20 26-58", (2026, 58), None),
]


@pytest.mark.parametrize(("text", "key", "kind"), MEASURED_SPOKEN)
def test_spoken_forms_normalize_to_one_identifier(text, key, kind) -> None:
    """A spoken number reads as the same (year, number) the agenda writes."""
    found = find_identifiers(text)
    assert [(identifier.key(), identifier.kind) for identifier in found] == [(key, kind)]


@pytest.mark.parametrize(
    ("text", "canonical"),
    [
        ("O-2026-54, A Bill For An Ordinance", "O-2026-54"),
        ("R-2026-52, A Resolution Of The Council", "R-2026-52"),
        ("resolution R202652", "R-2026-52"),
        ("resolution 2026-52", "R-2026-52"),
        ("202654", "2026-54"),
    ],
)
def test_written_and_glued_forms_read_the_same(text, canonical) -> None:
    """The written letter, the glued one and the bare digits all read alike."""
    found = find_identifiers(text)
    assert len(found) == 1
    assert found[0].canonical() == canonical


def test_written_identifiers_are_marked_as_written() -> None:
    """Only a text that writes the letter counts as written (spec 10.2)."""
    assert find_identifiers("O-2026-54")[0].is_written()
    assert find_identifiers("R202652")[0].is_written()
    assert not find_identifiers("ordinance 2026-54,")[0].is_written()


def test_the_written_letter_carries_the_kind() -> None:
    """An O is an ordinance and an R is a resolution, without any nearby word."""
    assert find_identifiers("O-2026-54")[0].kind == ORDINANCE
    assert find_identifiers("R-2026-52")[0].kind == RESOLUTION
    assert find_identifiers("R202652")[0].kind == RESOLUTION


def test_the_kind_word_is_the_nearest_one_before_the_number() -> None:
    """Two kind words in a row: the closer one owns the number."""
    found = find_identifiers("the resolution and the ordinance 2026-54")
    assert [identifier.kind for identifier in found] == [ORDINANCE]


def test_a_kind_word_after_the_number_is_not_reached() -> None:
    """The measured 1776 s case: ``202658, the ordinance to establish``.

    Spec 10.1 reads a number together with the kind word before it. A caption
    that puts the kind after the number leaves the kind unknown, which is
    honest: the number still matches an item whose own title gives the kind.
    """
    found = find_identifiers("in support of 202658, the ordinance to establish")
    assert [identifier.kind for identifier in found] == [None]


@pytest.mark.parametrize(
    "text",
    [
        "September 8th, 2026 um regular session meeting to order",
        "2027 Proposed Budget Presentation",
        "Financial Statements for the month ending July 31, 2026",
        "August 25, 2026 - Regular Session",
    ],
)
def test_a_bare_year_is_not_an_identifier(text) -> None:
    """Dates and years raise nothing: only a year with a number after it does."""
    assert find_identifiers(text) == ()


@pytest.mark.parametrize(
    ("text", "parts"),
    [
        ("now we are on to um agenda item 11 items removed from consent agenda", ("11",)),
        ("with item A1, ordinance 2026-54,", ("A", "1")),
        ("Item 9 C is", ("9", "C")),
        ("agenda item nine is", ("9",)),
        ("Item A, purchasing decision appeal", ("A",)),
        ("items B and E,", ("B",)),
        ("item 9B be removed", ("9", "B")),
    ],
)
def test_spoken_item_numbers_are_read_after_the_word_item(text, parts) -> None:
    """A number counts as an item number only after "item" (spec 10.2)."""
    found = find_item_references(text)
    assert [reference.parts for reference in found] == [parts]


@pytest.mark.parametrize(
    "text",
    [
        # The reading of the council's rules, 74 s to 94 s of the September 8,
        # 2026 video. It names agenda items in words the transcript never uses
        # as item numbers.
        "you are asked to come up and add your name to the speaker list for the "
        "specific item before the meeting",
        "each speaker is limited to three minutes",
        "number one we have a public invited to be heard",
        "if you would like to speak during first call",
    ],
)
def test_a_number_word_without_the_word_item_is_not_an_item_number(text) -> None:
    """Spec 10.2 asks for item numbers, not every number that is spoken."""
    assert find_item_references(text) == ()


def test_every_number_word_one_to_twenty_is_known() -> None:
    """Spec 10.2's list, spelled once, with no gaps in it."""
    assert sorted(NUMBER_WORDS.values()) == list(range(1, 21))


def test_significant_words_are_longer_than_four_letters_and_unique() -> None:
    """Spec 10.2 counts the words over four letters of a title."""
    assert significant_words("FIRST CALL - PUBLIC INVITED TO BE HEARD") == (
        "first",
        "public",
        "invited",
        "heard",
    )
    assert significant_words("2027 Proposed Budget Presentation") == (
        "proposed",
        "budget",
        "presentation",
    )
    assert significant_words("MAYOR AND COUNCIL COMMENTS MAYOR") == ("mayor", "council", "comments")


def test_words_of_keeps_the_words_only() -> None:
    """Punctuation and digits are dropped, letters and apostrophes are not."""
    assert words_of("Item 9 C is, O-2026-54.") == ("item", "c", "is", "o")
