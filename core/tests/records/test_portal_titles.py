"""What a portal title says about its body and its sitting (spec 6.2, 9.2).

Every title in the reading tests is a title the recorded 2026 list really
carries, read in place from the fixture of the oversight repository. The live
defect this file guards is written down in the two names it is given: the
portal listed three meetings for 2026-09-08 and the sync stored one, because
"City Council Pre-Session" was read as a body named "City Council Pre".

A title the jurisdiction has no body for is not a failure of this reading, and
the reading tests do not claim one: what they claim is that the words a title
uses for a *sitting* never end up inside a body name, and that two spellings of
one sitting give the same body.
"""

from __future__ import annotations

import json
import re

import pytest

from townrecord.records import portal

from .conftest import ARCHIVED_2026, needs

#: The words that name a sitting rather than the body holding it. A body name
#: that still ends in one of these has not been read properly.
SITTING_WORDS = (
    "continued meeting",
    "executive session",
    "joint meeting",
    "joint session",
    "pre session",
    "regular session",
    "special meeting",
    "special session",
    "study session",
    "work session",
    "workshop",
    "hearings",
    "meeting",
    "session",
)

#: The words a title uses to say the sitting did not happen (spec 9.5).
CANCELLATION_WORDS = ("cancelled", "canceled")

#: Titles of the recorded 2026 list, with the body each one names. The live
#: defect is the first pair: the sync stored a body called "City Council Pre".
RECORDED_TITLES = (
    ("City Council Regular Session", "City Council"),
    ("City Council Pre-Session", "City Council"),
    ("City Council Study Session", "City Council"),
    ("City Council Executive Session", "City Council"),
    # The sitting word comes out and what it sat with stays: who a joint
    # sitting sits with is not part of the name of the body, but no reading
    # here takes it off either. The title is reported as it reads.
    (
        "City Council Joint Meeting with Boulder County Commissioners",
        "City Council with Boulder County Commissioners",
    ),
    ("City Council Meeting CANCELLED", "City Council"),
    ("City Council Open Forum", "City Council Open Forum"),
    ("Board of Adjustment and Appeals - CANCELLED", "Board of Adjustment and Appeals"),
    ("CANCELLED Master Board of Appeals", "Master Board of Appeals"),
    ("MEETING CANCELLED ~ Master Board of Appeals", "Master Board of Appeals"),
    ("CANCELLED MASTER BOARD OF APPEALS - 02/04/2026", "MASTER BOARD OF APPEALS"),
    ("Cancelled - Parks and Recreation Advisory Board", "Parks and Recreation Advisory Board"),
    ("Golf Course Advisory Board", "Golf Course Advisory Board"),
    (
        "Special Meeting - Longmont Housing Authority Board of Commissioners",
        "Longmont Housing Authority Board of Commissioners",
    ),
    ("Special Meeting - Longmont Urban Renewal Authority", "Longmont Urban Renewal Authority"),
    ("Water Board Special Meeting - Applicant Interviews", "Water Board"),
    ("Library Board", "Library Board"),
)

#: The same sittings spelled the other way round. A portal writes both, so the
#: reading has to fold them together.
VARIANT_TITLES = (
    ("City Council Pre Session", "City Council"),
    ("City Council Work Session", "City Council"),
    ("City Council Special Meeting", "City Council"),
    ("City Council Joint Session with the County", "City Council with the County"),
    ("City Council- Study Session", "City Council"),
)

#: The meeting type each sitting word names (spec 6.2). All but the last two
#: are titles of the recorded list.
RECORDED_TYPES = (
    ("City Council Regular Session", "regular"),
    ("City Council", "regular"),
    ("City Council Study Session", "study"),
    ("City Council Executive Session", "executive"),
    ("City Council Special Meeting", "special"),
    ("City Council Joint Meeting with Boulder County Commissioners", "special"),
    ("Housing and Human Service Advisory Board - Special Meeting", "special"),
    ("City Council Pre-Session", "special"),
    ("City Council Work Session", "study"),
)


def recorded_titles() -> list[str]:
    """Every title of the recorded 2026 list, in the order it was recorded."""
    return [entry["title"] for entry in json.loads(ARCHIVED_2026.read_text(encoding="utf-8"))]


@pytest.mark.parametrize(("title", "wanted"), RECORDED_TITLES + VARIANT_TITLES)
def test_a_title_names_the_body_holding_the_sitting(title: str, wanted: str) -> None:
    assert portal.body_name(title) == wanted


@pytest.mark.parametrize(("title", "wanted"), RECORDED_TYPES)
def test_a_title_names_the_type_of_sitting(title: str, wanted: str) -> None:
    assert portal.meeting_type(title) == wanted


@needs(ARCHIVED_2026)
def test_no_recorded_title_leaves_a_sitting_word_in_its_body() -> None:
    """The whole recorded list, and nothing left of a sitting in any of it.

    The count is asserted so that this test is known to cover the list it
    claims: a trimmed recording would pass silently otherwise.
    """
    titles = recorded_titles()
    assert len(titles) == 241
    wrong: list[str] = []
    for title in titles:
        name = portal.body_name(title)
        folded = name.casefold()
        if not name:
            wrong.append(f"{title!r} names no body at all")
        elif any(word in folded for word in CANCELLATION_WORDS):
            wrong.append(f"{title!r} reads as {name!r}, a cancellation")
        elif any(re.search(rf"\b{word}\b", folded) for word in SITTING_WORDS):
            wrong.append(f"{title!r} reads as {name!r}, which carries a sitting word")
    assert wrong == []


@needs(ARCHIVED_2026)
def test_the_three_meetings_of_the_day_the_live_sync_missed_read_as_their_bodies() -> None:
    """The 2026-09-08 listing, which the live sync read three meetings of.

    One of the three was stored, one named a body the jurisdiction does not
    have (the Housing Authority, which the sync is right to skip), and the
    pre-session of the City Council was read as a body called "City Council
    Pre" and skipped with it. The list is read in place, and the entry ids are
    the portal's own.
    """
    recorded = json.loads(ARCHIVED_2026.read_text(encoding="utf-8"))
    that_day = {
        entry["id"]: entry["title"]
        for entry in recorded
        if entry["dateTime"].startswith("2026-09-08")
    }
    assert that_day == {
        3797: "Longmont Housing Authority Advisory Board",
        3710: "City Council Pre-Session",
        3709: "City Council Regular Session",
    }
    assert portal.body_name(that_day[3710]) == "City Council"
    assert portal.body_name(that_day[3709]) == "City Council"
    assert portal.body_name(that_day[3797]) == "Longmont Housing Authority Advisory Board"
