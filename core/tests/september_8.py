"""Where each item of the September 8, 2026 Longmont council meeting starts.

Checked by hand against the recorded caption track 3qfQAkAAC9U.en.srv3, not
copied from what the code printed. The aligner tests and the speaker tests
both hold the aligner to these.
"""

from __future__ import annotations

#: Where each item of the September 8, 2026 meeting starts, in milliseconds
#: into the video. Each one was checked by hand against the caption text at
#: that time: the chair reads the item's number or title there, or for the
#: clerk's published times (1., 3., 4.A and 13.) the business of the item is
#: under way there. Items not listed have no start: their title is never said
#: as a transition.
HAND_CHECKED_STARTS = {
    "1.": 0,  # "call ... regular session September 8th, 2026 ... to order"
    "3.": 67_000,  # "the rules for providing public comment are as followed"
    "4.A": 110_000,  # "to approve the minutes of the August 25th, 2026 regular session"
    "5.": 135_920,  # "We are now on to uh agenda agenda revisions and submission of documents"
    "7.": 215_200,  # "special reports and presentations. There are none."
    "8.": 219_280,  # "We are now on to first call. Public invited to be heard."
    "9.A": 3_668_240,  # "Item 9A is ordinance 2026-57"
    "9.B": 3_680_880,  # "Item 9 B is ordinance 2026-58"
    "9.C": 3_690_400,  # "Item 9 C is ordinance 2026-59"
    "9.D": 3_704_160,  # "ordinance 2026-60"
    "9.E": 3_717_920,  # "ordinance 2026-61"
    "9.F": 3_733_359,  # "2026-57, a resolution ... Longmont Gateway Northwest annexation"
    "9.G": 3_760_400,  # "9G is resolution 2026-58"
    "9.H": 3_775_040,  # "9H is resolution 2026-59"
    "9.I.1": 3_796_000,  # "I1 is resolution 2026-60"
    "9.I.2": 3_807_280,  # "I2 is resolution 2026-61"
    "9.I.3": 3_817_520,  # "93 is resolution 2026-62"
    "9.I.4": 3_828_880,  # "94 is resolution 2026-63"
    "10.": 3_967_520,  # "We are now on to items uh ordinances on second reading"
    "10.A": 3_972_400,  # "We have item A, first and main transit project"
    "10.A.1": 3_978_160,  # "item A1, ordinance 2026-54"
    "10.A.2": 4_415_920,  # "We are now on to item A2. Ordinance 2026-55"
    "10.A.3": 4_510_480,  # "We are now on to item A3, ordinance 2026-56"
    "11.": 4_657_920,  # "agenda item 11 items removed from consent agenda"
    "11.A": 4_668_960,  # "reconsideration of resolution 2026-52"
    "12.": 8_604_000,  # "general business."
    "12.A": 8_606_160,  # "Item A, purchasing decision appeal"
    "12.B": 10_313_040,  # "2027 proposed budget presentation"
    "13.": 13_639_000,  # "we are now on to final call. Public invited to be heard"
    # "do we have any other mayor and council comments? Go ahead." The clerk's
    # published time, 14,619 s less the 466 s offset, is the same 14,153 s.
    "14.": 14_153_199,
}

#: The items placed from the clerk's published times (spec 10.1, method 1).
#: Every other item in :data:`HAND_CHECKED_STARTS` is placed by what the chair
#: says (method 2).
CLERK_TIMED = frozenset({"1.", "3.", "4.A", "13."})


def expected_placement(numbers: object, *, clerk_times: bool = True) -> dict:
    """What each item of the meeting should be, as ``{number: (method, start_ms)}``.

    An item in the table above starts where the table says, by the clerk's
    time for the four the clerk timed and by the spoken record for the rest.
    Without the clerk's times (an agenda PDF has none) those four are not
    placed. Every other item is not placed.
    """
    expected = {}
    for number in numbers:  # type: ignore[attr-defined]
        start = HAND_CHECKED_STARTS.get(number)
        if start is None or (number in CLERK_TIMED and not clerk_times):
            expected[number] = ("none", None)
        elif number in CLERK_TIMED:
            expected[number] = ("html_video_times", start)
        else:
            expected[number] = ("spoken_transitions", start)
    return expected
