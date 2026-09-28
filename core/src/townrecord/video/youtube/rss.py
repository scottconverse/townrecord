"""The channel's public feed (spec 8.1 step 2).

``https://www.youtube.com/feeds/videos.xml?channel_id=...`` answers the
newest fifteen videos as Atom, with no key and no quota. It carries no
status at all, so every entry it gives is ``unknown`` for readiness: the feed
is a good way to learn that a meeting exists and a poor way to learn whether
it has happened.

The channel id appears twice in a feed and they are not spelled the same:
the feed element holds ``yt:channelId`` without the ``UC`` prefix, while each
entry holds the full id. This reader uses the entry id when it is there and
falls back to the feed's, completed with :data:`FEED_CHANNEL_ID_PREFIX`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx

from townrecord import pacing
from townrecord.video.youtube.listing import SOURCE_RSS, VideoError, VideoListing

RSS_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

#: What a feed read is called in the process pace's own log line
#: (:mod:`townrecord.pacing`). The feed is a YouTube request like any other.
PACE_WHAT = "the channel feed"

#: Every YouTube channel id starts with this, in the form the API uses.
FEED_CHANNEL_ID_PREFIX = "UC"

ATOM = "http://www.w3.org/2005/Atom"
YT = "http://www.youtube.com/xml/schemas/2015"

_ENTRY = f"{{{ATOM}}}entry"
_TITLE = f"{{{ATOM}}}title"
_PUBLISHED = f"{{{ATOM}}}published"
_VIDEO_ID = f"{{{YT}}}videoId"
_CHANNEL_ID = f"{{{YT}}}channelId"


def feed_channel_id(feed_value: str) -> str:
    """Complete the feed's short channel id (``H5_...``) into the API's form."""
    value = (feed_value or "").strip()
    if not value:
        return ""
    if value.startswith(FEED_CHANNEL_ID_PREFIX):
        return value
    return FEED_CHANNEL_ID_PREFIX + value


class RssLister:
    """Reads one channel's feed over an :class:`httpx.Client` the caller owns."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def feed_url(self, channel_id: str) -> str:
        return RSS_FEED_URL.format(channel_id=channel_id)

    def list_videos(self, channel_id: str) -> list[VideoListing]:
        """The channel's feed, newest first, as listings with no status."""
        if not channel_id:
            raise VideoError("the public feed needs a channel id, and none was given")
        url = self.feed_url(channel_id)
        # Spec 8.10: reading a feed is a YouTube request, so it takes its turn
        # in the process pace beside the captures and the status asks.
        pacing.pacer().wait(what=PACE_WHAT)
        try:
            response = self.client.get(url)
        except httpx.HTTPError as exc:
            raise VideoError(f"the YouTube feed could not be reached: {exc}") from exc
        if response.status_code != 200:
            if response.status_code == pacing.RATE_LIMIT_STATUS:
                # A feed answered 429 is the same answer as a capture answered
                # 429: the address is being held, so every YouTube job waits.
                pacing.pacer().rate_limited(what=PACE_WHAT, marker="HTTP 429")
            raise VideoError(f"the YouTube feed answered HTTP {response.status_code} for {url}")
        return self.parse_feed(response.content)

    def parse_feed(self, body: bytes) -> list[VideoListing]:
        """Parse feed bytes. An entry with no video id is not a video, so it is skipped."""
        try:
            root = ET.fromstring(body)
        except ET.ParseError as exc:
            raise VideoError(f"the YouTube feed is not XML this reader can read: {exc}") from exc

        feed_id = feed_channel_id(root.findtext(_CHANNEL_ID) or "")
        videos: list[VideoListing] = []
        for entry in root.findall(_ENTRY):
            video_id = (entry.findtext(_VIDEO_ID) or "").strip()
            if not video_id:
                continue
            channel_id = (entry.findtext(_CHANNEL_ID) or "").strip() or feed_id
            videos.append(
                VideoListing(
                    video_id=video_id,
                    title=(entry.findtext(_TITLE) or "").strip(),
                    published=(entry.findtext(_PUBLISHED) or "").strip() or None,
                    channel_id=channel_id,
                    source_method=SOURCE_RSS,
                )
            )
        if not videos:
            raise VideoError("the YouTube feed listed 0 videos: the feed was read but holds none")
        return videos
