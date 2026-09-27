"""The listing ladder: official API, then the public feed, then yt-dlp (spec 8.1).

Each step is a method of its own and answers with the same dataclass. The
ladder records which method produced the list, so the interface can say how
the channel was read (spec 8.1). A day with nothing from any method is a
failure that carries every method's reason, never a quiet zero.

Step 3, the channel tab HTML, is not part of this unit. The test below
checks that it is a TODO and not a stub that pretends to read a page.
"""

from __future__ import annotations

import inspect

import pytest

from townrecord.video.youtube import ladder as ladder_module
from townrecord.video.youtube.ladder import (
    YOUTUBE_CHANNEL_URL,
    LadderResult,
    ListingLadder,
)
from townrecord.video.youtube.listing import (
    SOURCE_DATA_API,
    SOURCE_LABELS,
    SOURCE_RSS,
    SOURCE_YTDLP,
    ListingFailed,
    NoVideosFound,
    QuotaBlocked,
    VideoError,
    VideoListing,
    source_label,
)

from .conftest import CITY_CHANNEL_ID


def a_video(video_id: str, source_method: str) -> VideoListing:
    return VideoListing(
        video_id=video_id,
        title="City Council Regular Session",
        published=None,
        channel_id=CITY_CHANNEL_ID,
        source_method=source_method,
    )


class FakeMethod:
    """One ladder step: it answers with a script and records how it was called."""

    def __init__(self, videos: list[VideoListing] | None = None, error: VideoError | None = None):
        self.videos = list(videos or [])
        self.error = error
        self.calls: list[tuple[object, ...]] = []

    def list_channel_videos(
        self, channel_id: str | None = None, handle: str | None = None, username: str | None = None
    ) -> list[VideoListing]:
        self.calls.append(("api", channel_id, handle, username))
        return self._answer()

    def list_videos(self, identifier: str) -> list[VideoListing]:
        self.calls.append(("feed", identifier))
        return self._answer()

    def _answer(self) -> list[VideoListing]:
        if self.error is not None:
            raise self.error
        return list(self.videos)


def test_the_official_api_answers_first() -> None:
    api = FakeMethod([a_video("api00000001", SOURCE_DATA_API)])
    feed = FakeMethod([a_video("rss00000001", SOURCE_RSS)])
    flat = FakeMethod([a_video("yt000000001", SOURCE_YTDLP)])

    result = ListingLadder(data_api=api, rss=feed, ytdlp=flat).list_videos(
        channel_id=CITY_CHANNEL_ID
    )

    assert isinstance(result, LadderResult)
    assert result.method == SOURCE_DATA_API
    assert [video.video_id for video in result.videos] == ["api00000001"]
    assert feed.calls == [] and flat.calls == [], "no later method is used"
    assert [attempt.method for attempt in result.attempts] == [SOURCE_DATA_API]
    assert result.attempts[0].ok is True
    assert result.attempts[0].count == 1


def test_the_interface_says_which_method_read_the_channel() -> None:
    """Spec 8.1: show it to the user, in these two words."""
    assert source_label(SOURCE_DATA_API) == "Read YouTube with the official API"
    assert source_label(SOURCE_RSS) == "Read the public feed instead"
    assert set(SOURCE_LABELS) == {SOURCE_DATA_API, SOURCE_RSS, SOURCE_YTDLP}

    api = FakeMethod(error=QuotaBlocked("the day's quota is used up"))
    feed = FakeMethod([a_video("rss00000001", SOURCE_RSS)])

    result = ListingLadder(data_api=api, rss=feed).list_videos(channel_id=CITY_CHANNEL_ID)

    assert result.method == SOURCE_RSS
    assert result.label == "Read the public feed instead"
    assert result.attempts[0].reason == "the day's quota is used up"
    assert result.attempts[0].ok is False


def test_a_used_up_quota_does_not_stop_the_ladder() -> None:
    api = FakeMethod(error=QuotaBlocked("the API is blocked until 2026-09-28T00:00:00-07:00"))
    feed = FakeMethod([a_video("rss00000001", SOURCE_RSS)])

    result = ListingLadder(data_api=api, rss=feed).list_videos(channel_id=CITY_CHANNEL_ID)

    assert result.method == SOURCE_RSS
    assert "2026-09-28" in result.attempts[0].reason
    assert len(api.calls) == 1, "a blocked API is not asked twice"


def test_ytdlp_is_reached_when_both_public_readers_fail() -> None:
    api = FakeMethod(error=VideoError("HTTP 403"))
    feed = FakeMethod(error=VideoError("the feed listed 0 videos"))
    flat = FakeMethod([a_video("yt000000001", SOURCE_YTDLP)])

    result = ListingLadder(data_api=api, rss=feed, ytdlp=flat).list_videos(
        channel_id=CITY_CHANNEL_ID
    )

    assert result.method == SOURCE_YTDLP
    assert [attempt.method for attempt in result.attempts] == [
        SOURCE_DATA_API,
        SOURCE_RSS,
        SOURCE_YTDLP,
    ]
    assert [attempt.ok for attempt in result.attempts] == [False, False, True]
    assert result.attempts[1].reason == "the feed listed 0 videos"


def test_zero_from_every_method_is_a_failure_with_every_reason() -> None:
    api = FakeMethod(error=QuotaBlocked("the day's quota is used up"))
    feed = FakeMethod(error=VideoError("the feed listed 0 videos"))
    flat = FakeMethod(error=VideoError("yt-dlp listed 0 videos"))

    with pytest.raises(NoVideosFound) as caught:
        ListingLadder(data_api=api, rss=feed, ytdlp=flat).list_videos(channel_id=CITY_CHANNEL_ID)

    message = str(caught.value)
    for method in (SOURCE_DATA_API, SOURCE_RSS, SOURCE_YTDLP):
        assert method in message, f"the failure names {method}"
    assert "quota" in message
    assert "0 videos" in message
    assert [attempt.method for attempt in caught.value.attempts] == [
        SOURCE_DATA_API,
        SOURCE_RSS,
        SOURCE_YTDLP,
    ]
    assert isinstance(caught.value, VideoError)


def test_a_method_that_lists_nothing_is_a_reason_not_a_success() -> None:
    """Zero from the API is not an answer; the ladder moves on."""
    api = FakeMethod([])
    feed = FakeMethod([a_video("rss00000001", SOURCE_RSS)])

    result = ListingLadder(data_api=api, rss=feed).list_videos(channel_id=CITY_CHANNEL_ID)

    assert result.method == SOURCE_RSS
    assert result.attempts[0].ok is False
    assert result.attempts[0].count == 0
    assert "0 videos" in result.attempts[0].reason


def test_each_step_refuses_an_empty_list_on_its_own() -> None:
    """The steps are testable apart from the ladder, and they fail loudly."""
    api = FakeMethod([])
    feed = FakeMethod([])
    flat = FakeMethod([])
    ladder = ListingLadder(data_api=api, rss=feed, ytdlp=flat)

    with pytest.raises(ListingFailed) as from_api:
        ladder.from_data_api(channel_id=CITY_CHANNEL_ID)
    with pytest.raises(ListingFailed) as from_feed:
        ladder.from_rss(CITY_CHANNEL_ID)
    with pytest.raises(ListingFailed) as from_flat:
        ladder.from_ytdlp_flat(YOUTUBE_CHANNEL_URL.format(channel_id=CITY_CHANNEL_ID))

    assert "0 videos" in str(from_api.value)
    assert "0 videos" in str(from_feed.value)
    assert "0 videos" in str(from_flat.value)


def test_each_step_answers_with_the_same_dataclass() -> None:
    api = FakeMethod([a_video("api00000001", SOURCE_DATA_API)])
    feed = FakeMethod([a_video("rss00000001", SOURCE_RSS)])
    flat = FakeMethod([a_video("yt000000001", SOURCE_YTDLP)])
    ladder = ListingLadder(data_api=api, rss=feed, ytdlp=flat)

    answers = (
        ladder.from_data_api(channel_id=CITY_CHANNEL_ID),
        ladder.from_rss(CITY_CHANNEL_ID),
        ladder.from_ytdlp_flat("https://www.youtube.com/channel/" + CITY_CHANNEL_ID),
    )

    for videos in answers:
        assert isinstance(videos, list)
        assert all(isinstance(video, VideoListing) for video in videos)
    assert [videos[0].source_method for videos in answers] == [
        SOURCE_DATA_API,
        SOURCE_RSS,
        SOURCE_YTDLP,
    ]


def test_a_step_that_is_not_configured_records_that_reason() -> None:
    """A missing API key is a reason, never a silent skip."""
    feed = FakeMethod([a_video("rss00000001", SOURCE_RSS)])

    result = ListingLadder(rss=feed).list_videos(channel_id=CITY_CHANNEL_ID)

    assert result.method == SOURCE_RSS
    assert result.attempts[0].ok is False
    assert "API key" in result.attempts[0].reason
    assert "not configured" in result.attempts[0].reason

    with pytest.raises(NoVideosFound) as caught:
        ListingLadder().list_videos(channel_id=CITY_CHANNEL_ID)

    message = str(caught.value)
    assert message.count("not configured") == 3, "every step says why it did not run"


def test_each_step_is_handed_the_channel_it_was_given() -> None:
    api = FakeMethod(error=VideoError("HTTP 403"))
    feed = FakeMethod(error=VideoError("the feed listed 0 videos"))
    flat = FakeMethod([a_video("yt000000001", SOURCE_YTDLP)])

    ListingLadder(data_api=api, rss=feed, ytdlp=flat).list_videos(
        channel_id=CITY_CHANNEL_ID, handle="@citychannel"
    )

    assert api.calls == [("api", CITY_CHANNEL_ID, "@citychannel", None)]
    assert feed.calls == [("feed", CITY_CHANNEL_ID)]
    assert flat.calls == [("feed", YOUTUBE_CHANNEL_URL.format(channel_id=CITY_CHANNEL_ID))]


def test_a_channel_url_is_used_when_one_is_given() -> None:
    api = FakeMethod(error=VideoError("HTTP 403"))
    feed = FakeMethod(error=VideoError("the feed listed 0 videos"))
    flat = FakeMethod([a_video("yt000000001", SOURCE_YTDLP)])

    result = ListingLadder(data_api=api, rss=feed, ytdlp=flat).list_videos(
        channel_id=CITY_CHANNEL_ID, channel_url="https://www.youtube.com/@citychannel"
    )

    assert result.method == SOURCE_YTDLP
    assert feed.calls == [("feed", CITY_CHANNEL_ID)], "the feed still needs the channel id"
    assert flat.calls == [("feed", "https://www.youtube.com/@citychannel")]
    assert YOUTUBE_CHANNEL_URL == "https://www.youtube.com/channel/{channel_id}"


def test_the_channel_tab_html_step_is_a_todo_and_not_a_stub() -> None:
    """Spec 8.1 step 3 belongs to another unit. Nothing here pretends."""
    source = inspect.getsource(ladder_module)

    assert "TODO" in source
    assert "8.1 step 3" in source
    assert "channel tab" in source.lower()
    assert not hasattr(ladder_module, "from_channel_html")
    assert not [name for name in vars(ListingLadder) if "html" in name.lower()]
    for name in ("from_data_api", "from_rss", "from_ytdlp_flat"):
        assert callable(getattr(ListingLadder, name))
