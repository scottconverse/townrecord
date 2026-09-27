"""The Channel RSS listing method (spec 8.1 step 2).

Fixtures: the two recorded feeds, 15 entries each, captured September 27,
2026. The feed-level `yt:channelId` is `H5_wkpLrKYb1JuUk6-UdNg` (no UC
prefix) while each entry's `yt:channelId` carries the full id. The entry
value is used; the feed value is only a fallback, and then the missing UC
prefix is put back.
"""

from __future__ import annotations

import httpx
import pytest

from townrecord.video.youtube.listing import SOURCE_RSS, VideoError, VideoListing
from townrecord.video.youtube.rss import (
    FEED_CHANNEL_ID_PREFIX,
    RSS_FEED_URL,
    RssLister,
    feed_channel_id,
)

from .conftest import CITY_CHANNEL_ID, FIXTURE_FOLDER, PUBLIC_MEDIA_CHANNEL_ID, offline_client


def make_lister(body: bytes, status: int = 200) -> tuple[RssLister, list[httpx.Request]]:
    """An RssLister over a fake feed, with the requests it made."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, content=body)

    return RssLister(offline_client(transport=httpx.MockTransport(handle))), requests


def test_the_feed_url_names_the_channel(city_rss: bytes) -> None:
    lister, requests = make_lister(city_rss)

    lister.list_videos(CITY_CHANNEL_ID)

    assert requests[0].url == httpx.URL(RSS_FEED_URL.format(channel_id=CITY_CHANNEL_ID))
    assert "channel_id=" + CITY_CHANNEL_ID in str(requests[0].url)


@pytest.mark.parametrize(
    ("fixture_name", "channel_id"),
    [
        ("rss-UCH5_wkpLrKYb1JuUk6-UdNg.xml", CITY_CHANNEL_ID),
        ("rss-UCXFW3IRzfCc6q_XM-uutAmw.xml", PUBLIC_MEDIA_CHANNEL_ID),
    ],
)
def test_each_recorded_feed_lists_fifteen_videos(fixture_name: str, channel_id: str) -> None:
    lister = RssLister(offline_client())
    videos = lister.parse_feed((FIXTURE_FOLDER / fixture_name).read_bytes())

    assert len(videos) == 15
    assert all(video.video_id for video in videos)
    assert {video.channel_id for video in videos} == {channel_id}
    assert {video.source_method for video in videos} == {SOURCE_RSS}


def test_the_first_recorded_entry_reads_exactly(city_rss: bytes) -> None:
    lister, _ = make_lister(city_rss)

    first = lister.list_videos(CITY_CHANNEL_ID)[0]

    assert first == VideoListing(
        video_id="DDrMAVD9N34",
        title="2360 Mountain Brook Drive - McDonald's Neighborhood Meeting",
        published="2026-09-25T00:28:54+00:00",
        channel_id=CITY_CHANNEL_ID,
        source_method=SOURCE_RSS,
    )
    assert first.published == "2026-09-25T00:28:54+00:00", "the published time is kept as given"


def test_the_entry_channel_id_wins_over_the_feed_channel_id(city_rss: bytes) -> None:
    """Each entry carries the full UC id; the feed's id is missing the prefix."""
    lister = RssLister(offline_client())
    root_text = city_rss.decode("utf-8")
    assert "<yt:channelId>H5_wkpLrKYb1JuUk6-UdNg</yt:channelId>" in root_text, (
        "the recorded feed-level value really does lack the UC prefix"
    )

    videos = lister.parse_feed(city_rss)

    assert {video.channel_id for video in videos} == {CITY_CHANNEL_ID}


def test_a_feed_without_entry_ids_falls_back_to_the_feed_id() -> None:
    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <id>yt:channel:H5_wkpLrKYb1JuUk6-UdNg</id>
 <yt:channelId>H5_wkpLrKYb1JuUk6-UdNg</yt:channelId>
 <title>Test channel</title>
 <entry>
  <id>yt:video:aaaaaaaaaaa</id>
  <yt:videoId>aaaaaaaaaaa</yt:videoId>
  <title>City Council Regular Session - 1 January 2026</title>
  <published>2026-01-02T00:00:00+00:00</published>
 </entry>
</feed>
"""
    videos = RssLister(offline_client()).parse_feed(feed)

    assert [video.channel_id for video in videos] == [CITY_CHANNEL_ID]
    assert FEED_CHANNEL_ID_PREFIX == "UC"
    assert feed_channel_id("H5_wkpLrKYb1JuUk6-UdNg") == CITY_CHANNEL_ID
    assert feed_channel_id(CITY_CHANNEL_ID) == CITY_CHANNEL_ID


def test_entities_in_titles_are_decoded(city_rss: bytes, public_media_rss: bytes) -> None:
    city = [video.title for video in RssLister(offline_client()).parse_feed(city_rss)]
    public = [video.title for video in RssLister(offline_client()).parse_feed(public_media_rss)]

    assert (
        "Historic Walking Tour, Bilingual Community Resource Fair, & More"
        " | This is Longmont 9/17/2026" in city
    )
    assert "Housing and Human Services Advisory Health & Wellbeing Hearing Sept. 17, 2026" in city
    assert "Longmont Planning & Zoning - Regular Session - September 23, 2026" in public


def test_an_empty_feed_is_a_failure_with_a_reason() -> None:
    empty = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">
 <yt:channelId>UCH5_wkpLrKYb1JuUk6-UdNg</yt:channelId>
 <title>Test channel</title>
</feed>
"""
    lister = RssLister(offline_client())

    with pytest.raises(VideoError) as caught:
        lister.parse_feed(empty)

    assert "0 videos" in str(caught.value)


def test_a_broken_feed_is_a_failure_with_a_reason() -> None:
    lister = RssLister(offline_client())

    with pytest.raises(VideoError) as caught:
        lister.parse_feed(b"<feed><entry>")

    assert "XML" in str(caught.value) or "xml" in str(caught.value)


def test_a_status_other_than_200_is_a_failure(city_rss: bytes) -> None:
    lister, _ = make_lister(city_rss, status=429)

    with pytest.raises(VideoError) as caught:
        lister.list_videos(CITY_CHANNEL_ID)

    assert "429" in str(caught.value)


def test_an_unreachable_feed_is_a_failure(city_rss: bytes) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    lister = RssLister(offline_client(transport=httpx.MockTransport(handle)))

    with pytest.raises(VideoError):
        lister.list_videos(CITY_CHANNEL_ID)
