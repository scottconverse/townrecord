"""The YouTube Data API listing method (spec 8.1 step 1).

Three calls of one quota unit each, a key in the header and never in a URL,
no search.list, and quota counted before the reply is read.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from townrecord.video.youtube import data_api
from townrecord.video.youtube.data_api import (
    API_KEY_HEADER,
    CHANNEL_PART,
    CHANNELS_PATH,
    DATA_API_HOST,
    PLAYLIST_ITEMS_PATH,
    PLAYLIST_PART,
    VIDEO_PART,
    VIDEOS_PATH,
    DataApiClient,
    QuotaLedger,
    next_pacific_midnight,
    parse_iso_duration,
)
from townrecord.video.youtube.listing import SOURCE_DATA_API, QuotaBlocked, VideoError

from .conftest import (
    CITY_CHANNEL_ID,
    CITY_UPLOADS_PLAYLIST,
    TEST_API_KEY,
    TEST_HANDLE,
    FakeDataApi,
    offline_client,
)

PACIFIC = ZoneInfo("America/Los_Angeles")

#: 13:30 Pacific on September 27, 2026, inside daylight saving time.
SUMMER_NOW = datetime(2026, 9, 27, 20, 30, tzinfo=UTC)


class FixedClock:
    """A clock a test can move."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_the_three_calls_happen_in_order(api: FakeDataApi) -> None:
    api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert api.paths() == [CHANNELS_PATH, PLAYLIST_ITEMS_PATH, VIDEOS_PATH]


def test_every_call_carries_the_key_in_the_header_and_never_in_the_url(
    api: FakeDataApi,
) -> None:
    api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert len(api.requests) == 3
    for request in api.requests:
        assert request.headers[API_KEY_HEADER] == TEST_API_KEY
        assert "key" not in {name.lower() for name in request.url.params}
        assert TEST_API_KEY not in str(request.url)


def test_search_list_is_never_called(api: FakeDataApi) -> None:
    """search.list costs 100 units. The ladder must not touch it."""
    api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert not [path for path in api.paths() if path.endswith("/search")]
    assert "/search" not in inspect.getsource(data_api), "no call to search.list may exist"


def test_the_channel_call_asks_for_the_uploads_playlist(api: FakeDataApi) -> None:
    api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID)

    params = api.requests[0].url.params
    assert params["part"] == CHANNEL_PART
    assert params["id"] == CITY_CHANNEL_ID
    assert "forHandle" not in params and "forUsername" not in params


def test_a_handle_and_a_username_are_asked_for_by_name(api: FakeDataApi) -> None:
    api.client().list_channel_videos(handle=TEST_HANDLE)
    assert api.requests[0].url.params["forHandle"] == TEST_HANDLE
    assert "id" not in api.requests[0].url.params

    api.requests.clear()
    api.client().list_channel_videos(username="testchannel")
    assert api.requests[0].url.params["forUsername"] == "testchannel"


def test_the_playlist_call_asks_for_fifty_items(api: FakeDataApi) -> None:
    api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID)

    params = api.requests[1].url.params
    assert params["part"] == PLAYLIST_PART
    assert params["playlistId"] == CITY_UPLOADS_PLAYLIST
    assert params["maxResults"] == "50"


def test_the_videos_call_asks_for_ids_and_status_parts(api: FakeDataApi) -> None:
    api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID)

    params = api.requests[2].url.params
    assert params["part"] == VIDEO_PART
    assert params["id"] == "jhsFsEz0P5A,DUzTpP66lk0,uHFuCfd5MVQ"


def test_the_listing_carries_the_fields_the_ladder_needs(api: FakeDataApi) -> None:
    videos = api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert len(videos) == 3
    first = videos[0]
    assert first.video_id == "jhsFsEz0P5A"
    assert first.title == "City Council Regular Session - 22 September 2026"
    assert first.published == "2026-09-23T18:53:52Z"
    assert first.channel_id == CITY_CHANNEL_ID
    assert first.source_method == SOURCE_DATA_API
    assert first.duration_s == 14290, "PT3H58M10S"
    assert first.live_broadcast_content == "none"
    assert videos[2].live_broadcast_content == "upcoming"
    assert videos[1].duration_s == 22501, "PT6H15M1S"


def test_the_api_host_is_the_documented_one(api: FakeDataApi) -> None:
    api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert DATA_API_HOST == "www.googleapis.com"
    assert {request.url.host for request in api.requests} == {DATA_API_HOST}


def test_three_calls_cost_three_units(api: FakeDataApi) -> None:
    client = api.client()
    client.list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert client.quota.units_spent == 3
    assert client.quota.daily_limit == 10_000


def test_quota_is_counted_before_the_reply_is_read(api: FakeDataApi) -> None:
    """A call that fails still spent its unit: the count happens first."""
    api.channels_status = 500
    client = api.client()

    with pytest.raises(VideoError):
        client.list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert client.quota.units_spent == 1


def test_a_used_up_day_makes_no_request(api: FakeDataApi) -> None:
    clock = FixedClock(SUMMER_NOW)
    client = api.client(quota=QuotaLedger(daily_limit=2, clock=clock))

    with pytest.raises(QuotaBlocked) as caught:
        client.list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert len(api.requests) == 2, "two units was the whole day"
    assert "quota" in str(caught.value).lower()


def test_a_quota_refusal_blocks_the_api_until_pacific_midnight(api: FakeDataApi) -> None:
    clock = FixedClock(SUMMER_NOW)
    healthy_body = api.channels_body
    api.channels_status = 403
    api.channels_body = {
        "error": {
            "code": 403,
            "message": "The request cannot be completed because you have exceeded your quota.",
            "errors": [{"reason": "quotaExceeded", "message": "quota"}],
        }
    }
    client = api.client(quota=QuotaLedger(clock=clock))

    with pytest.raises(QuotaBlocked) as caught:
        client.list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert len(api.requests) == 1, "the refusal is not retried"
    assert client.quota.blocked_until == datetime(2026, 9, 28, tzinfo=PACIFIC)
    assert str(caught.value).find("2026-09-28") >= 0

    with pytest.raises(QuotaBlocked):
        client.list_channel_videos(channel_id=CITY_CHANNEL_ID)
    assert len(api.requests) == 1, "the block holds until midnight"

    clock.now = datetime(2026, 9, 28, 7, 0, 30, tzinfo=UTC)
    # The refusal was for one Pacific day only, so the next day's API answers.
    api.channels_status = 200
    api.channels_body = healthy_body
    assert not client.quota.is_blocked()
    client.list_channel_videos(channel_id=CITY_CHANNEL_ID)
    assert len(api.requests) == 4, "a new Pacific day is three calls again"
    assert client.quota.units_spent == 3, "the unit count starts over"


def test_rate_limiting_is_not_a_day_long_block(api: FakeDataApi) -> None:
    """Spec 8.3: HTTP 429 is a paced retry, not a used up day."""
    api.channels_status = 429
    api.channels_body = {"error": {"code": 429, "errors": [{"reason": "rateLimitExceeded"}]}}
    clock = FixedClock(SUMMER_NOW)
    client = api.client(quota=QuotaLedger(clock=clock))

    with pytest.raises(VideoError) as caught:
        client.list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert not isinstance(caught.value, QuotaBlocked)
    assert client.quota.blocked_until is None
    assert "429" in str(caught.value)


def test_a_channel_without_a_match_is_a_failure(api: FakeDataApi) -> None:
    api.channels_body = {"items": []}

    with pytest.raises(VideoError) as caught:
        api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert "channel" in str(caught.value).lower()


def test_a_channel_without_an_uploads_playlist_is_a_failure(api: FakeDataApi) -> None:
    api.channels_body = {
        "items": [{"id": CITY_CHANNEL_ID, "contentDetails": {"relatedPlaylists": {}}}]
    }

    with pytest.raises(VideoError):
        api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID)


def test_an_empty_uploads_playlist_is_an_empty_listing(api: FakeDataApi) -> None:
    """The ladder, not this method, decides that zero videos is a failure."""
    api.playlist_body = {"items": []}

    assert api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID) == []


def test_an_api_key_is_required_and_never_defaulted(api: FakeDataApi) -> None:
    with pytest.raises(ValueError):
        DataApiClient("   ", offline_client())

    parameters = inspect.signature(DataApiClient.__init__).parameters
    assert parameters["api_key"].default is inspect.Parameter.empty


def test_an_identifier_is_required(api: FakeDataApi) -> None:
    with pytest.raises(ValueError):
        api.client().list_channel_videos()


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("PT3H58M10S", 14290),
        ("PT6H15M1S", 22501),
        ("PT0S", 0),
        ("P0D", 0),
        ("P1DT2H3M4S", 93784),
        ("PT4H7S", 14407),
        ("", None),
        (None, None),
        ("not a duration", None),
    ],
)
def test_iso_durations_read(text: str | None, seconds: int | None) -> None:
    assert parse_iso_duration(text) == seconds


def test_pacific_midnight_is_the_quota_reset() -> None:
    summer = datetime(2026, 9, 27, 20, 30, tzinfo=UTC)
    winter = datetime(2026, 12, 27, 20, 30, tzinfo=UTC)

    assert next_pacific_midnight(summer) == datetime(2026, 9, 28, tzinfo=PACIFIC)
    assert next_pacific_midnight(summer).utcoffset() == timedelta(hours=-7)
    assert next_pacific_midnight(winter) == datetime(2026, 12, 28, tzinfo=PACIFIC)
    assert next_pacific_midnight(winter).utcoffset() == timedelta(hours=-8)
    assert next_pacific_midnight(summer) - summer == timedelta(hours=10, minutes=30)


def test_the_ledger_reports_what_is_left(api: FakeDataApi) -> None:
    ledger = QuotaLedger(daily_limit=10, clock=FixedClock(SUMMER_NOW))
    ledger.spend(4)

    assert ledger.units_spent == 4
    assert ledger.units_left == 6
    assert not ledger.is_blocked()


def test_a_client_without_a_key_in_the_url_uses_the_test_host(api: FakeDataApi) -> None:
    api.client().list_channel_videos(channel_id=CITY_CHANNEL_ID)

    assert all(request.url.scheme == "https" for request in api.requests)
