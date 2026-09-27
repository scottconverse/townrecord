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
from townrecord.repo import get_source, insert_body

from .conftest import ARCHIVED_2026, needs

#: How many distinct body names the recorded 2026 list reads as, once the
#: annotations are off (241 titles). Before K3 it was 55.
RECORDED_DISTINCT_NAMES = 44

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

#: Titles of the recorded list the K3 decisions clean, with the body name each
#: one reads as afterwards. Every title is quoted from the recording.
CLEANED_TITLES = (
    # A status tail is not part of a name. The status is kept on the meeting
    # row instead (``meetings.is_cancelled`` and ``meetings.is_continued``),
    # which is where a reader asks whether a sitting happened as listed.
    ("Water Board POSTPONED to March 23", "Water Board"),
    ("Local Licensing Authority- CONTINUED TO DATE TBD", "Local Licensing Authority"),
    # A trailing month, a trailing date and a trailing acronym that repeats
    # the name are not part of it.
    ("Transportation Advisory Board May Meeting", "Transportation Advisory Board"),
    ("Transportation Advisory Board July Meeting", "Transportation Advisory Board"),
    ("Longmont Urban Renewal Authority (LURA)", "Longmont Urban Renewal Authority"),
    ("Longmont Urban Renewal Authority (LURA) 5/5/26", "Longmont Urban Renewal Authority"),
    # A modality word is not part of a name, wherever it stands: at the front,
    # at the back, or in the portal's own marker.
    ("REMOTE Sustainability Advisory Board", "Sustainability Advisory Board"),
    ("IN PERSON Sustainability Advisory Board", "Sustainability Advisory Board"),
    ("Planning and Zoning Commission (livestreamed)", "Planning and Zoning Commission"),
    ("Airport Advisory Board *Livestream*", "Airport Advisory Board"),
    ("Airport Advisory Board **Livestreamed**", "Airport Advisory Board"),
    (
        "Airport Noise Improvement Project Community Meeting - VIRTUAL",
        "Airport Noise Improvement Project Community",
    ),
    # A leading type word is the sitting's type, not the body's name.
    ("SPECIAL WATER BOARD MEETING - Spring Water Symposium", "WATER BOARD"),
    # A title that is nothing but a modality word and an event names no body:
    # the tidied title is what is reported for it, as it was before.
    ("VIRTUAL - Black Rock Coffee Notice of Neighborhood Meeting", "VIRTUAL"),
    ("Virtual - Mountain Crest Subdivision - Notice of Neighborhood Meeting ", "Virtual"),
)

#: The event titles the reading leaves alone, with the name each one keeps.
#: Each names an event or a panel rather than a standing body, and the name is
#: kept for the caller to report, which is the documented fallback.
EVENT_TITLES = (
    ("Town Hall: What the City Can and Cannot Do About Airport Noise", "Town Hall"),
    ("New Board Orientation and Onboarding", "New Board Orientation and Onboarding"),
    (
        "Art in Public Places Selection Panel - Kimbark Mural Project",
        "Art in Public Places Selection Panel",
    ),
    ("AIPP Kimbark Mural Artist Presentations", "AIPP Kimbark Mural Artist Presentations"),
    (
        "City of Longmont Official City Flag Redesign Selection Panel Meeting",
        "City of Longmont Official City Flag Redesign Selection Panel",
    ),
    ("Public Meeting - Proposed Design Standards & Construction Specifications", "Public"),
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


@pytest.mark.parametrize(("title", "wanted"), CLEANED_TITLES)
def test_a_title_is_read_down_to_the_body_it_names(title: str, wanted: str) -> None:
    """The annotations a portal hangs on a name are not part of the name."""
    assert portal.body_name(title) == wanted


#: The titles of the recorded list that carry a status tail, with the status
#: the tail names. The status is read out of the title and kept on the meeting
#: row: ``meetings.is_cancelled`` for the first and ``meetings.is_continued``
#: for the other two.
STATUS_TITLES = (
    ("Water Board POSTPONED to March 23", "postponed", "Water Board"),
    ("Local Licensing Authority- CONTINUED TO DATE TBD", "continued", "Local Licensing Authority"),
    ("City Council Regular Session - CANCELLED", "cancelled", "City Council"),
)


@pytest.mark.parametrize(("title", "word", "name"), STATUS_TITLES)
def test_a_status_tail_is_read_as_the_meeting_s_status(title: str, word: str, name: str) -> None:
    """A status tail leaves the name, and is not lost with it.

    "Water Board POSTPONED to March 23" is a Water Board sitting that was
    postponed, and "Local Licensing Authority- CONTINUED TO DATE TBD" is a
    Local Licensing Authority sitting that was continued. Which of the two it
    is has a column of its own on the meeting (spec 6.2), so the name does not
    carry it and the reading says it instead.
    """
    status = portal.continuation_signal(title) or portal.cancellation_signal(title, ())
    assert status is not None and word in status
    assert portal.body_name(title) == name


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


#: The words a cleaned body name cannot carry: a sitting word, a cancellation
#: word, a status word, or a word that says how the sitting was held.
ANNOTATION_WORDS = (
    SITTING_WORDS
    + CANCELLATION_WORDS
    + ("postponed", "continued", "virtual", "remote", "in person", "livestream", "livestreamed")
)

#: The names the reading reports for a title that names no body at all: the
#: modality markers of the 5 recorded titles that are nothing but a marker and
#: an event. They are not body names, and the test counts them rather than
#: letting them through unread.
MODALITY_FALLBACKS = ("virtual",)

#: The month names a trailing month would be written with.
MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)


@needs(ARCHIVED_2026)
def test_no_recorded_title_keeps_an_annotation_in_its_body() -> None:
    """The whole recorded list, and no name carrying what is not a name.

    The count is asserted so that this test is known to cover the list it
    claims: a trimmed recording would pass silently otherwise.
    """
    titles = recorded_titles()
    assert len(titles) == 241
    wrong: list[str] = []
    fallbacks = 0
    for title in titles:
        name = portal.body_name(title)
        folded = name.casefold()
        if any(re.search(rf"\b{word}\b", folded) for word in ANNOTATION_WORDS):
            # The one exception is the reading's own fallback: a title that is
            # nothing but a modality marker and an event names no body at all,
            # and the tidied title is what is reported for it. Those are
            # counted here rather than waved through.
            if folded in MODALITY_FALLBACKS:
                fallbacks += 1
            else:
                wrong.append(f"{title!r} reads as {name!r}, which carries an annotation")
        elif re.search(r"\d{1,2}/\d{1,2}/\d{2,4}$", name):
            wrong.append(f"{title!r} reads as {name!r}, which ends in a date")
        elif any(re.search(rf"\b{month}$", folded) for month in MONTHS):
            wrong.append(f"{title!r} reads as {name!r}, which ends in a month")
        elif re.search(r"\([A-Z]{2,6}\)$", name):
            wrong.append(f"{title!r} reads as {name!r}, which ends in an acronym")
    assert wrong == []
    assert fallbacks == 5, "the recorded list has 5 titles of a marker and an event"


@needs(ARCHIVED_2026)
def test_the_recorded_list_reads_as_this_many_distinct_body_names() -> None:
    """The whole list again, counted, so a name that drifts shows up here.

    Three names of the reading that were one title each are gone with it: the
    modality-only titles read as VIRTUAL and Virtual, which are what the
    reading reports for a title that names no body at all.
    """
    names: dict[str, int] = {}
    for title in recorded_titles():
        name = portal.body_name(title)
        names[name] = names.get(name, 0) + 1
    assert sum(names.values()) == 241
    assert len(names) == RECORDED_DISTINCT_NAMES
    # The name that covers the most titles is the City Council's, which the
    # portal spells the same way in every one of its sittings.
    assert max(names, key=lambda name: names[name]) == "City Council"
    assert names["City Council"] == 39


def test_a_body_is_found_however_the_title_spells_it(conn, area) -> None:
    """Case, an ampersand and the portal's own markers are folded away (rule 6).

    "Planning & Zoning Commission" is the same body as "Planning and Zoning
    Commission", and the portal writes both. Before this reading the
    ampersand spelling found no body and the meeting was skipped with a name
    the jurisdiction does hold.
    """
    insert_body(conn, jurisdiction_id=area.jurisdiction_id, name="Planning and Zoning Commission")
    source = get_source(conn, area.portal_id)
    for title in (
        "Planning and Zoning Commission Regular Session",
        "Planning & Zoning Commission Regular Session",
        "PLANNING & ZONING COMMISSION - CANCELLED",
        "Planning and Zoning Commission (livestreamed)",
        "Planning and Zoning Commission (livestreamed) ",
    ):
        found = portal.resolve_body(conn, source, title)
        assert found is not None, f"{title!r} names a body the jurisdiction holds"
        assert found.name == "Planning and Zoning Commission"


def test_the_body_side_of_a_gathering_dash_is_the_body(conn, area) -> None:
    """The side of the dash that names a body is the body (rule 5).

    "Annual Retreat - Historic Preservation Commission" gathers in the
    Commission, so it is a Commission sitting. The reading takes the side that
    is a body of the jurisdiction, and leaves a title whose other side is no
    body of it alone: what "Town Hall: What the City Can and Cannot Do About
    Airport Noise" names is an event, and no body of this jurisdiction.
    """
    insert_body(conn, jurisdiction_id=area.jurisdiction_id, name="Historic Preservation Commission")
    source = get_source(conn, area.portal_id)

    found = portal.resolve_body(conn, source, "Annual Retreat - Historic Preservation Commission")

    assert found is not None
    assert found.name == "Historic Preservation Commission"
    assert (
        portal.resolve_body(
            conn, source, "Town Hall: What the City Can and Cannot Do About Airport Noise"
        )
        is None
    )


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
