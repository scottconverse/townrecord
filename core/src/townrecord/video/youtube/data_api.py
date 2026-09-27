"""The official YouTube Data API: three calls of one quota unit each (spec 8.1).

The plan is fixed and small. Ask the channel for its uploads playlist, ask
that playlist for its newest items, ask for the status of those videos. Each
call costs one unit of a 10,000 unit day (spec 8.3), which is why the ladder
counts the unit *before* it reads the reply: a call that fails still spent it.

Two habits the tests hold to:

* the key travels in the ``x-goog-api-key`` header and never in a URL, so it
  cannot end up in a log, a proxy or a browser history;
* ``search.list`` is never called. It costs 100 units a call, and this
  ladder does not need it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from townrecord.video.youtube.listing import (
    SOURCE_DATA_API,
    QuotaBlocked,
    VideoError,
    VideoListing,
)

API_KEY_HEADER = "x-goog-api-key"
DATA_API_HOST = "www.googleapis.com"
DATA_API_ORIGIN = f"https://{DATA_API_HOST}"
API_VERSION_PATH = "/youtube/v3"

CHANNELS_PATH = f"{API_VERSION_PATH}/channels"
PLAYLIST_ITEMS_PATH = f"{API_VERSION_PATH}/playlistItems"
VIDEOS_PATH = f"{API_VERSION_PATH}/videos"

CHANNEL_PART = "snippet,contentDetails"
PLAYLIST_PART = "snippet,contentDetails"
VIDEO_PART = "snippet,contentDetails,status"

#: Spec 8.1: the newest items first, and no more than this per call.
MAX_RESULTS = 50

#: Spec 8.3: 10,000 units a day, reset at Pacific midnight.
DAILY_QUOTA_UNITS = 10_000
PACIFIC = ZoneInfo("America/Los_Angeles")

#: The error reasons the API gives when the day, not the moment, is the problem.
QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})

#: A retry later is fine for these; the day is not over.
PACED_RETRY_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


def parse_iso_duration(text: str | None) -> int | None:
    """Read an ISO 8601 duration as whole seconds, or ``None`` if it is not one.

    ``PT3H58M10S`` is 14290. ``P0D`` is 0. The API uses this for a video's
    length: it is the field that tells a finished recording from a premiere.
    """
    if not isinstance(text, str) or not text:
        return None
    match = _DURATION_RE.match(text.strip())
    if match is None:
        return None
    parts = match.groupdict()
    seconds = 0.0
    seconds += float(parts["days"] or 0) * 86400
    seconds += float(parts["hours"] or 0) * 3600
    seconds += float(parts["minutes"] or 0) * 60
    seconds += float(parts["seconds"] or 0)
    return int(seconds)


def next_pacific_midnight(now: datetime) -> datetime:
    """The next midnight in Pacific time: when the API's day starts over."""
    local = now.astimezone(PACIFIC)
    tomorrow = local.date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=PACIFIC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class QuotaLedger:
    """How much of the API's day is left, and whether the door is shut.

    The day is Pacific, so the reset happens at Pacific midnight whatever the
    machine's own timezone is. A refusal from the API itself (403 with a
    quota reason) shuts the door for the rest of that day.
    """

    def __init__(
        self,
        daily_limit: int = DAILY_QUOTA_UNITS,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.daily_limit = daily_limit
        self.units_spent = 0
        self.blocked_until: datetime | None = None
        self._clock = clock
        self._day: Any = None

    def _roll_over(self) -> None:
        """A new Pacific day clears the count and the block."""
        today = self._clock().astimezone(PACIFIC).date()
        if self._day is None:
            self._day = today
        elif today != self._day:
            self._day = today
            self.units_spent = 0
            self.blocked_until = None

    def is_blocked(self) -> bool:
        """True while the API must not be called at all."""
        self._roll_over()
        if self.blocked_until is None:
            return False
        return self._clock() < self.blocked_until

    @property
    def units_left(self) -> int:
        self._roll_over()
        return max(self.daily_limit - self.units_spent, 0)

    def block(self) -> datetime:
        """Shut the door until the next Pacific midnight, and say when that is."""
        self.blocked_until = next_pacific_midnight(self._clock())
        return self.blocked_until

    def spend(self, units: int = 1) -> None:
        """Count ``units`` before the call is made.

        Raises :class:`QuotaBlocked` when the day cannot pay, so the ladder
        moves to the next method instead of hammering a closed door.
        """
        self._roll_over()
        now = self._clock()
        if self.blocked_until is not None and now < self.blocked_until:
            raise QuotaBlocked(
                "the YouTube Data API is blocked until"
                f" {self.blocked_until.isoformat()}: the day's quota is used up"
            )
        if self.units_spent + units > self.daily_limit:
            blocked_until = self.block()
            raise QuotaBlocked(
                f"the YouTube Data API's daily quota of {self.daily_limit} units is"
                f" used up; the API is blocked until {blocked_until.isoformat()}"
            )
        self.units_spent += units


class DataApiClient:
    """The three-call plan, over an :class:`httpx.Client` the caller owns."""

    def __init__(
        self,
        api_key: str,
        client: httpx.Client,
        *,
        quota: QuotaLedger | None = None,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("a YouTube Data API key is required, and is never defaulted")
        self.api_key = api_key.strip()
        self.client = client
        self.quota = quota if quota is not None else QuotaLedger()

    # -- the plan ------------------------------------------------------------

    def list_channel_videos(
        self,
        channel_id: str | None = None,
        handle: str | None = None,
        username: str | None = None,
    ) -> list[VideoListing]:
        """The channel's newest uploads, read in three calls.

        An empty answer is an empty list, not a failure: the ladder decides
        that zero videos from every method is the failure.
        """
        if not any((channel_id, handle, username)):
            raise ValueError("a channel id, a handle, or a username is required")

        uploads = self._uploads_playlist(channel_id, handle, username)
        entries = self._playlist_items(uploads)
        if not entries:
            return []
        facts = self._video_facts([entry.video_id for entry in entries])
        videos: list[VideoListing] = []
        for entry in entries:
            live_broadcast_content, duration_s = facts.get(entry.video_id, (None, None))
            videos.append(
                VideoListing(
                    video_id=entry.video_id,
                    title=entry.title,
                    published=entry.published,
                    channel_id=entry.channel_id,
                    source_method=SOURCE_DATA_API,
                    duration_s=duration_s,
                    live_broadcast_content=live_broadcast_content,
                )
            )
        return videos

    def _uploads_playlist(
        self, channel_id: str | None, handle: str | None, username: str | None
    ) -> str:
        params: dict[str, str] = {"part": CHANNEL_PART}
        if channel_id:
            params["id"] = channel_id
            which = f"channel id {channel_id}"
        elif handle:
            params["forHandle"] = handle
            which = f"handle {handle}"
        else:
            params["forUsername"] = username or ""
            which = f"username {username}"

        body = self._get_json(CHANNELS_PATH, params)
        items = body.get("items")
        if not isinstance(items, list) or not items:
            raise VideoError(f"the YouTube Data API knows no channel for the {which}")
        related = items[0].get("contentDetails", {}).get("relatedPlaylists", {})
        uploads = related.get("uploads") if isinstance(related, Mapping) else None
        if not uploads:
            raise VideoError(f"the YouTube Data API gave no uploads playlist for the {which}")
        return str(uploads)

    def _playlist_items(self, uploads_playlist: str) -> list[_PlaylistEntry]:
        body = self._get_json(
            PLAYLIST_ITEMS_PATH,
            {
                "part": PLAYLIST_PART,
                "playlistId": uploads_playlist,
                "maxResults": str(MAX_RESULTS),
            },
        )
        items = body.get("items")
        if not isinstance(items, list):
            return []
        entries: list[_PlaylistEntry] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            video_id = item.get("contentDetails", {}).get("videoId")
            if not video_id:
                continue
            snippet = item.get("snippet") or {}
            entries.append(
                _PlaylistEntry(
                    video_id=str(video_id),
                    title=str(snippet.get("title") or ""),
                    published=item.get("contentDetails", {}).get("videoPublishedAt")
                    or snippet.get("publishedAt"),
                    channel_id=str(snippet.get("channelId") or ""),
                )
            )
        return entries

    def _video_facts(self, video_ids: Sequence[str]) -> dict[str, tuple[Any, int | None]]:
        """The status and the length of each video, by id."""
        body = self._get_json(VIDEOS_PATH, {"part": VIDEO_PART, "id": ",".join(video_ids)})
        facts: dict[str, tuple[Any, int | None]] = {}
        items = body.get("items")
        if not isinstance(items, list):
            return facts
        for item in items:
            if not isinstance(item, Mapping) or not item.get("id"):
                continue
            snippet = item.get("snippet") or {}
            details = item.get("contentDetails") or {}
            facts[str(item["id"])] = (
                snippet.get("liveBroadcastContent"),
                parse_iso_duration(details.get("duration")),
            )
        return facts

    # -- one call ------------------------------------------------------------

    def _get_json(self, path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        """One call: count the unit, then make it, then read the reply."""
        self.quota.spend(1)
        try:
            response = self.client.get(
                DATA_API_ORIGIN + path,
                params=dict(params),
                headers={API_KEY_HEADER: self.api_key},
            )
        except httpx.HTTPError as exc:
            raise VideoError(f"the YouTube Data API could not be reached: {exc}") from exc
        if response.status_code != 200:
            self._raise_for_error(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise VideoError(
                f"the YouTube Data API answered HTTP {response.status_code}"
                " with something that is not JSON"
            ) from exc
        if not isinstance(body, Mapping):
            raise VideoError("the YouTube Data API answered JSON that is not an object")
        return body

    def _raise_for_error(self, response: httpx.Response) -> None:
        """Turn the API's own error body into a reason the report can show."""
        body: Any = None
        try:
            body = response.json()
        except ValueError:
            body = None
        error = body.get("error") if isinstance(body, Mapping) else None
        error = error if isinstance(error, Mapping) else {}
        message = str(error.get("message") or "")
        errors = error.get("errors")
        reasons = {
            str(item.get("reason"))
            for item in (errors if isinstance(errors, list) else [])
            if isinstance(item, Mapping) and item.get("reason")
        }
        if reasons & QUOTA_REASONS or (response.status_code == 403 and "quota" in message.lower()):
            blocked_until = self.quota.block()
            raise QuotaBlocked(
                "the YouTube Data API refused the call:"
                f" {message or 'the day quota is used up'}"
                f" The API is blocked until {blocked_until.isoformat()}."
            )
        if reasons & PACED_RETRY_REASONS or response.status_code == 429:
            raise VideoError(
                f"the YouTube Data API answered HTTP {response.status_code}"
                f" ({message or 'too many requests'}): try again shortly,"
                " the day is not used up"
            )
        raise VideoError(
            f"the YouTube Data API answered HTTP {response.status_code}:"
            f" {message or ', '.join(sorted(reasons)) or 'no reason given'}"
        )


class _PlaylistEntry:
    """One row of the playlist call, before the status call fills it in."""

    __slots__ = ("video_id", "title", "published", "channel_id")

    def __init__(self, video_id: str, title: str, published: str | None, channel_id: str) -> None:
        self.video_id = video_id
        self.title = title
        self.published = published
        self.channel_id = channel_id
