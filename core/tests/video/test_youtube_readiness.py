"""Readiness: is this meeting upcoming, live, finished, or not yet known (spec 8.2)?

The order is the spec's: the Data API's `liveBroadcastContent` with the
duration first, then yt-dlp's `live_status`. `unknown` means "waiting for
status metadata (will retry)" and is never a failure.
"""

from __future__ import annotations

import pytest

from townrecord.video.youtube.listing import SOURCE_RSS, VideoListing
from townrecord.video.youtube.readiness import (
    LIVE_BROADCAST_READINESS,
    LIVE_STATUS_READINESS,
    READINESS_FINISHED,
    READINESS_LIVE,
    READINESS_UNKNOWN,
    READINESS_UPCOMING,
    READINESS_VALUES,
    readiness,
    readiness_reason,
)
from townrecord.video.youtube.rss import RssLister

from .conftest import CITY_CHANNEL_ID, offline_client


def listing(**overrides: object) -> VideoListing:
    """A listing with only the fields a test cares about set."""
    fields: dict[str, object] = {
        "video_id": "vid00000001",
        "title": "City Council Regular Session",
        "published": None,
        "channel_id": CITY_CHANNEL_ID,
        "source_method": SOURCE_RSS,
    }
    fields.update(overrides)
    return VideoListing(**fields)  # type: ignore[arg-type]


def test_the_four_outcomes_are_the_documented_ones() -> None:
    assert READINESS_VALUES == (
        READINESS_UPCOMING,
        READINESS_LIVE,
        READINESS_FINISHED,
        READINESS_UNKNOWN,
    )
    assert (READINESS_UPCOMING, READINESS_LIVE, READINESS_FINISHED, READINESS_UNKNOWN) == (
        "upcoming",
        "live",
        "finished",
        "unknown",
    )


@pytest.mark.parametrize(
    ("live_status", "expected"),
    [
        ("is_upcoming", READINESS_UPCOMING),
        ("is_live", READINESS_LIVE),
        ("was_live", READINESS_FINISHED),
        ("not_live", READINESS_FINISHED),
        # post_live: the stream has ended, the recording is still being made.
        # Reading it as "finished" would send the download step at a file that
        # is not there yet, so it is "waiting" instead. A person might draw
        # this line differently; the reason string says which line was drawn.
        ("post_live", READINESS_UNKNOWN),
    ],
)
def test_every_ytdlp_live_status_maps(live_status: str, expected: str) -> None:
    subject = listing(live_status=live_status)

    assert readiness(subject) == expected
    assert readiness(subject) in READINESS_VALUES
    assert LIVE_STATUS_READINESS[live_status] == expected


@pytest.mark.parametrize(
    ("live_broadcast_content", "duration_s", "expected"),
    [
        ("live", None, READINESS_LIVE),
        ("upcoming", 0, READINESS_UPCOMING),
        ("none", 14290, READINESS_FINISHED),
        # Published, but no length yet: nothing has been recorded. Wait.
        ("none", 0, READINESS_UNKNOWN),
        ("none", None, READINESS_UNKNOWN),
    ],
)
def test_the_data_api_answer_comes_first(
    live_broadcast_content: str, duration_s: int | None, expected: str
) -> None:
    subject = listing(live_broadcast_content=live_broadcast_content, duration_s=duration_s)

    assert readiness(subject) == expected


def test_the_data_api_answer_wins_over_ytdlp() -> None:
    """Spec 8.2 lists the Data API first, so it decides when it can."""
    subject = listing(live_broadcast_content="none", duration_s=14290, live_status="is_live")

    assert readiness(subject) == READINESS_FINISHED


def test_ytdlp_decides_when_the_data_api_cannot() -> None:
    """A finished recording with no length yet is not a verdict. yt-dlp's is."""
    subject = listing(live_broadcast_content="none", duration_s=0, live_status="is_live")

    assert readiness(subject) == READINESS_LIVE
    assert LIVE_BROADCAST_READINESS == {"live": READINESS_LIVE, "upcoming": READINESS_UPCOMING}


def test_an_unknown_broadcast_value_falls_through_to_ytdlp() -> None:
    subject = listing(live_broadcast_content="something_new", live_status="was_live")

    assert readiness(subject) == READINESS_FINISHED


def test_a_listing_with_no_metadata_is_unknown_and_never_a_failure() -> None:
    subject = listing()

    assert readiness(subject) == READINESS_UNKNOWN
    assert "will retry" in readiness_reason(subject)
    assert "metadata" in readiness_reason(subject)


def test_a_length_alone_is_not_a_verdict() -> None:
    """A premiere has a length before it plays. Without a status, wait."""
    subject = listing(duration_s=14290)

    assert readiness(subject) == READINESS_UNKNOWN


def test_an_unrecognized_live_status_is_unknown_with_its_value_named() -> None:
    subject = listing(live_status="some_new_status")

    assert readiness(subject) == READINESS_UNKNOWN
    assert "some_new_status" in readiness_reason(subject)


def test_post_live_explains_itself() -> None:
    reason = readiness_reason(listing(live_status="post_live"))

    assert "post_live" in reason
    assert "will retry" in reason


def test_an_info_json_decides_by_its_live_status(video_info: dict[str, object]) -> None:
    assert readiness(info=video_info) == READINESS_FINISHED
    assert "not_live" in readiness_reason(info=video_info)

    live = dict(video_info, live_status="is_live")
    assert readiness(info=live) == READINESS_LIVE


def test_an_info_json_without_a_status_is_unknown() -> None:
    assert readiness(info={"id": "vid00000001", "duration": 14407}) == READINESS_UNKNOWN
    assert readiness(info=None) == READINESS_UNKNOWN


def test_nothing_about_a_status_is_ever_a_failure(city_rss: bytes) -> None:
    """A feed carries no status at all, so every feed entry is `unknown`.

    The feed is still a good way to learn that a meeting exists. It cannot
    say whether the meeting is over, so another method answers that question.
    """
    videos = RssLister(offline_client()).parse_feed(city_rss)

    assert len(videos) == 15
    assert {readiness(video) for video in videos} == {READINESS_UNKNOWN}
