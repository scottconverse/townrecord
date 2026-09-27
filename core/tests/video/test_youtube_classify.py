"""Telling a meeting apart from everything else a channel posts (spec 7.2 step 6).

Both recorded feeds are listed below, every title with its verdict, in feed
order. The keyword list and the skip list are data, so the tests check that a
caller can replace them and that the seed lists themselves are unchanged.

Three titles are known misses and are named as such at the bottom of the file.
They are recorded, not hidden: the classifier matches phrases with word
boundaries, so a title that renames or misspells a body falls through.
"""

from __future__ import annotations

import pytest

from townrecord.video.youtube.classify import (
    DEFAULT_MEETING_KEYWORDS,
    DEFAULT_SKIP_TITLES,
    MeetingClassifier,
    MeetingSettings,
    MeetingVerdict,
)
from townrecord.video.youtube.rss import RssLister

from .conftest import offline_client

#: The City of Longmont channel, feed order: (title, is it a meeting?).
CITY_TITLES: tuple[tuple[str, bool], ...] = (
    ("2360 Mountain Brook Drive - McDonald's Neighborhood Meeting", False),
    ("Planning and Zoning Commission 9/23/26", True),
    ("City Council Regular Session - 22 September 2026", True),
    ("Housing and Human Services Advisory Health & Wellbeing Hearing Sept. 17, 2026", False),
    ("This Week in Council, Sept. 22, 2026", False),
    ("2026 LevelUp Longmont Esports Tournament", False),
    ("Sustainability Advisory Board Meeting Sept. 16, 2026", True),
    ("Longmont Urban Renewal Authority (LURA) Meeting, Sept. 15, 2026", True),
    ("Parks and Recreation Advisory Board Meeting Sept. 14, 2026", True),
    ("Transportation Advisory Board Meeting Sept. 14, 2026", True),
    ("8979 Nelson Road Neighborhood Meeting", False),
    ("Colorado 150 – Kuner-Empson Cannery Building", False),
    (
        "Historic Walking Tour, Bilingual Community Resource Fair, & More"
        " | This is Longmont 9/17/2026",
        False,
    ),
    ("Planning and Zoning Commission 9/16/26", True),
    ("City Council Study Session - 15 September 2026", True),
)

#: The Longmont Public Media channel, feed order.
PUBLIC_MEDIA_TITLES: tuple[tuple[str, bool], ...] = (
    ("Voices of Change: Councilwoman (2019, NR) Conversation", False),
    ("Comfy Couch Performance Circle -  September 2026", False),
    ("Longmont Planning & Zoning - Regular Session - September 23, 2026", True),
    ("City Council Regular Session - September 22, 2026", True),
    ("Museum Advisory Board - September 2026", True),
    ("Library Advisory Board - September 2026", True),
    ("Water Advisory Board - September 2026", True),
    (
        "City of Longmont Council & Boulder County Commissioners Joint Meeting"
        " - September 21, 2026",
        False,
    ),
    ("Latino Homeownership in Longmont - September 11, 2026", False),
    ("2026 LevelUp Longmont Esports Tournament", False),
    ("Longmont Urban Renewal Authroity - September 15, 2026", False),
    ("Arts in Public Places Commission - September 17, 2026", True),
    ("Longmont Planning & Zoning - Regular Session - September 16, 2026", True),
    ("City Council Study Session - September 15, 2026", True),
    ("Parks and Recreation Advisory Board - September 2026", True),
)

#: What each channel's list decides, in the words the interface shows.
CITY_MEETINGS = 8
PUBLIC_MEDIA_MEETINGS = 9


def classifier(**kwargs: object) -> MeetingClassifier:
    return MeetingClassifier(**kwargs)  # type: ignore[arg-type]


def test_the_seed_lists_are_the_documented_ones() -> None:
    assert DEFAULT_MEETING_KEYWORDS == (
        "City Council",
        "Planning and Zoning",
        "Advisory Board",
        "Commission",
        "Authority",
        "Study Session",
        "Regular Session",
    )
    assert DEFAULT_SKIP_TITLES == (
        "This Week in Council",
        "Neighborhood Meeting",
        "Esports",
        "This is Longmont",
    )
    assert isinstance(DEFAULT_MEETING_KEYWORDS, tuple), "a constant no caller can edit in place"


@pytest.mark.parametrize(("title", "expected"), CITY_TITLES)
def test_every_city_title_has_a_verdict(title: str, expected: bool) -> None:
    assert classifier().is_meeting(title) is expected


@pytest.mark.parametrize(("title", "expected"), PUBLIC_MEDIA_TITLES)
def test_every_public_media_title_has_a_verdict(title: str, expected: bool) -> None:
    assert classifier().is_meeting(title) is expected


def test_the_tables_list_every_recorded_title_in_feed_order(
    city_rss: bytes, public_media_rss: bytes
) -> None:
    """A table that drifts from the fixtures stops matching the feed."""
    lister = RssLister(offline_client())

    city = [video.title for video in lister.parse_feed(city_rss)]
    public = [video.title for video in lister.parse_feed(public_media_rss)]

    assert city == [title for title, _ in CITY_TITLES]
    assert public == [title for title, _ in PUBLIC_MEDIA_TITLES]


def test_the_seed_lists_classify_the_recorded_feeds(
    city_rss: bytes, public_media_rss: bytes
) -> None:
    lister = RssLister(offline_client())
    classifier_ = classifier()

    city = classifier_.filter_meetings(lister.parse_feed(city_rss))
    public = classifier_.filter_meetings(lister.parse_feed(public_media_rss))

    assert len(city) == CITY_MEETINGS
    assert len(public) == PUBLIC_MEDIA_MEETINGS
    assert all(video.video_id for video in city + public)


def test_a_phrase_matches_on_word_boundaries() -> None:
    classifier_ = classifier()

    assert classifier_.is_meeting("City Council Regular Session")
    assert classifier_.is_meeting("city council regular session"), "the case does not matter"
    assert not classifier_.is_meeting("City Councilwoman Jones speaks")
    assert not classifier_.is_meeting("The Commissioners met"), "Commission is not Commissioners"
    assert not classifier_.is_meeting("Longmont Urban Renewal Authroity"), "Authority, misspelled"
    assert classifier_.is_meeting("Commission and Authority joint session")
    assert classifier_.matches_keywords("Planning and Zoning Commission 9/23/26") == (
        "Planning and Zoning",
        "Commission",
    )


def test_the_skip_list_is_read_first_and_wins() -> None:
    """A city neighborhood meeting is not a council meeting."""
    verdict = classifier().verdict("City Council Neighborhood Meeting")

    assert verdict.is_meeting is False
    assert verdict.matched_skips == ("Neighborhood Meeting",)
    assert "Neighborhood Meeting" in verdict.reason
    assert verdict.matched_keywords == ("City Council",), "the keyword is still reported"


def test_a_verdict_says_why() -> None:
    meeting = classifier().verdict("City Council Study Session - 15 September 2026")
    other = classifier().verdict("Colorado 150 – Kuner-Empson Cannery Building")

    assert meeting.is_meeting is True
    assert meeting.reason == "Read as a meeting: matched 'City Council' and 'Study Session'."
    assert meeting.matched_keywords == ("City Council", "Study Session")
    assert other.is_meeting is False
    assert other.reason == "Not read as a meeting: no keyword matched."
    assert isinstance(meeting, MeetingVerdict)


def test_the_lists_are_data_a_caller_can_replace() -> None:
    custom = classifier(keywords=("Town Hall",), skip=("Town Hall Budget",))

    assert custom.is_meeting("Town Hall: Ward 3") is True
    assert custom.is_meeting("City Council Regular Session") is False, "the seed list was replaced"
    assert custom.is_meeting("Town Hall Budget Hearing") is False
    assert DEFAULT_MEETING_KEYWORDS[0] == "City Council", "the seed list is unchanged"


def test_an_explicitly_empty_list_matches_nothing() -> None:
    """An empty keyword list is a caller's choice: then nothing is a meeting."""
    none_at_all = classifier(keywords=(), skip=())

    assert none_at_all.is_meeting("City Council Regular Session") is False
    assert none_at_all.is_meeting("This Week in Council, Sept. 22, 2026") is False
    assert none_at_all.is_meeting("2026 LevelUp Longmont Esports Tournament") is False


def test_the_lists_come_from_settings() -> None:
    """The keywords are settings, not constants a user cannot reach."""
    settings = MeetingSettings(
        meeting_keywords=("Board of Adjustment",), meeting_skip_titles=("Training",)
    )

    from_settings = MeetingClassifier.from_settings(settings)
    assert from_settings.is_meeting("Board of Adjustment - September 2026") is True
    assert from_settings.is_meeting("Board of Adjustment Training") is False

    from_mapping = MeetingClassifier.from_settings(
        {"meeting_keywords": ["Board of Adjustment"], "meeting_skip_titles": []}
    )
    assert from_mapping.is_meeting("Board of Adjustment - September 2026") is True
    assert from_mapping.is_meeting("Board of Adjustment Training") is True

    assert MeetingClassifier.from_settings({}).is_meeting("City Council") is True


def test_the_join_meeting_and_the_hearing_are_known_misses() -> None:
    """Three titles a person would call meetings and the seed lists do not.

    Naming them here keeps the limit visible: `Commissioners` is not
    `Commission`, `Authroity` is a misspelling on the other channel's upload,
    and the health and wellbeing hearing names no body the seed list holds.
    The fix is a keyword in settings, not a special case in the code.
    """
    classifier_ = classifier()
    missed = [
        "City of Longmont Council & Boulder County Commissioners Joint Meeting"
        " - September 21, 2026",
        "Housing and Human Services Advisory Health & Wellbeing Hearing Sept. 17, 2026",
        "Longmont Urban Renewal Authroity - September 15, 2026",
    ]

    for title in missed:
        assert classifier_.is_meeting(title) is False, title

    # The same meeting, spelled as the feed spells it, is read correctly.
    assert classifier_.is_meeting("Longmont Urban Renewal Authority (LURA) Meeting") is True

    # And the settings fix works with no code change.
    fixed = classifier(
        keywords=(*DEFAULT_MEETING_KEYWORDS, "Joint Meeting", "Health & Wellbeing", "Authroity")
    )
    assert all(fixed.is_meeting(title) for title in missed)


def test_the_ampersand_form_needs_its_second_keyword() -> None:
    """`Planning and Zoning` does not match `Planning & Zoning`.

    Both recorded titles with the ampersand also say `Regular Session`, so
    they are read correctly. A title with only the ampersand form is missed,
    and that is written down here rather than papered over.
    """
    classifier_ = classifier()

    assert classifier_.is_meeting("Longmont Planning & Zoning - Regular Session") is True
    assert classifier_.matches_keywords("Longmont Planning & Zoning - Regular Session") == (
        "Regular Session",
    )
    assert classifier_.is_meeting("Longmont Planning & Zoning - September 2026") is False
