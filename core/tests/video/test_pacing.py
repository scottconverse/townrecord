"""Every YouTube request of the video side waits on the process pace.

Spec 8.10 says "Pace every request", and a listing, a status ask and a feed
read are all requests. The measured live run of 2026-09-27 made 7 status asks
beside 7 captures in 80 seconds, so a pace that only covered captures would
still have asked YouTube fourteen times from one address.

The pace a test watches is the process pace of :mod:`tests.conftest`, which
records what each caller waited to do.
"""

from __future__ import annotations

import pytest

from tests.capture.fakes import RATE_LIMIT_STDERR
from tests.conftest import RecordingPacer
from townrecord.video.youtube.listing import VideoError
from townrecord.video.youtube.readiness import READINESS_FINISHED
from townrecord.video.youtube.status import StatusAsker
from townrecord.video.youtube.ytdlp import YTDLP_TABS, YtdlpFlatLister

from .conftest import CITY_CHANNEL_ID, FakeRunner
from .test_youtube_rss import make_lister
from .test_youtube_status import URL, VIDEO_ID, StubApi, listing
from .test_youtube_ytdlp import an_entry, listing_bytes

STATUS_ASK = "a status ask"
LISTING = "a channel listing"
FEED = "the channel feed"

#: The suffix the fake runner keys one request on: the last argument's last
#: segment, which for a watch address is the address's own tail.
WATCH_SUFFIX = f"watch?v={VIDEO_ID}"


def test_a_status_ask_waits_for_the_pace(pace: RecordingPacer) -> None:
    """Spec 8.2's third step is a request, and it is the one nobody paced."""
    asker = StatusAsker(runner=FakeRunner())

    asker.ask(video_id=VIDEO_ID, url=URL)

    assert pace.waits == [STATUS_ASK]


def test_a_status_ask_the_api_answers_still_waits_once(pace: RecordingPacer) -> None:
    """The first step is a request too, so the first ask waits before it."""
    asker = StatusAsker(runner=FakeRunner(), api=StubApi(listing(live_status="was_live")))

    answer = asker.ask(video_id=VIDEO_ID, url=URL)

    assert answer.readiness == READINESS_FINISHED
    assert pace.waits == [STATUS_ASK]


def test_a_rate_limited_status_ask_holds_the_other_youtube_jobs(
    pace: RecordingPacer,
) -> None:
    """A 429 is the address's, whatever asked it.

    The wording is the one recorded from a real run, which lives with the
    capture fakes because that is where it was first needed.
    """
    runner = FakeRunner()
    runner.answers[WATCH_SUFFIX] = (b"", 1, RATE_LIMIT_STDERR)
    asker = StatusAsker(runner=runner)

    asker.ask(video_id=VIDEO_ID, url=URL)

    assert pace.holds == [(STATUS_ASK, None)]


def test_a_listing_waits_once_for_each_tab(pace: RecordingPacer) -> None:
    """Two tabs are two requests, so the second one waits too."""
    runner = FakeRunner()
    runner.answers["streams"] = (listing_bytes([an_entry("vid00000001", "A meeting")]), 0, "")

    YtdlpFlatLister(runner=runner, interpreter="python").list_videos(
        f"https://www.youtube.com/channel/{CITY_CHANNEL_ID}"
    )

    assert pace.waits == [LISTING]


def test_a_listing_that_tries_both_tabs_waits_for_both(pace: RecordingPacer) -> None:
    """A failed tab is a request that happened, so the next one still waits."""
    runner = FakeRunner()
    runner.answers["streams"] = (b"", 1, "ERROR: nope")
    runner.answers["videos"] = (listing_bytes([an_entry("vid00000001", "A meeting")]), 0, "")

    YtdlpFlatLister(runner=runner, interpreter="python").list_videos(
        f"https://www.youtube.com/channel/{CITY_CHANNEL_ID}"
    )

    assert len(YTDLP_TABS) == 2
    assert pace.waits == [LISTING, LISTING]


def test_a_feed_read_waits_for_the_pace(city_rss: bytes, pace: RecordingPacer) -> None:
    """The feed reader of spec 8.1 step 2 is a request over the portal client."""
    lister, requests = make_lister(city_rss)

    lister.list_videos(CITY_CHANNEL_ID)

    assert len(requests) == 1
    assert pace.waits == [FEED]


def test_a_rate_limited_feed_holds_the_other_youtube_jobs(pace: RecordingPacer) -> None:
    """A feed answered 429 is the same answer as a capture answered 429."""
    lister, _requests = make_lister(b"", status=429)

    with pytest.raises(VideoError):
        lister.list_videos(CITY_CHANNEL_ID)

    assert pace.holds == [(FEED, None)]
