"""The listing ladder: try each way of reading a channel, in order (spec 8.1).

Four ways, two of which are built here plus one more in a later unit:

1. the official Data API (a key, and ten thousand units a day);
2. the channel's public feed (no key, no quota, no status);
3. the channel tab HTML (``/streams`` as a page, for a reader with no tooling);
4. yt-dlp reading the tabs (no key, no quota, needs node).

Each step is a method of its own, so each can be tested alone, and every one
of them answers with the same list of
:class:`~townrecord.video.youtube.listing.VideoListing`. The ladder records
which step answered and why each earlier step did not, so the interface can
tell the user how the channel was read (spec 8.1) and the report can explain
a thin day.

Zero videos from every step is a failure carrying every reason. A method that
lists nothing is a reason to move on, never a quiet "this channel is empty".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from townrecord.video.youtube.listing import (
    SOURCE_DATA_API,
    SOURCE_RSS,
    SOURCE_YTDLP,
    LadderAttempt,
    ListingFailed,
    NoVideosFound,
    VideoError,
    VideoListing,
    source_label,
)

#: The channel's page, from its id. yt-dlp and the tab HTML both want this.
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/channel/{channel_id}"


@dataclass(frozen=True)
class LadderResult:
    """What the ladder answered, and how it got there."""

    method: str
    label: str
    videos: tuple[VideoListing, ...]
    attempts: tuple[LadderAttempt, ...]

    def reason(self, method: str) -> str:
        """Why the step ``method`` did or did not answer, in plain words."""
        for attempt in self.attempts:
            if attempt.method == method:
                return attempt.reason
        return ""


def _require_videos(videos: Sequence[VideoListing], who: str) -> list[VideoListing]:
    """A step's own refusal: an empty answer is a failure, not a thin day."""
    answer = list(videos)
    if not answer:
        raise ListingFailed(f"{who} listed 0 videos")
    return answer


class ListingLadder:
    """The steps, in order. Any step may be left out; then it says so."""

    def __init__(
        self,
        *,
        data_api: object | None = None,
        rss: object | None = None,
        ytdlp: object | None = None,
    ) -> None:
        self.data_api = data_api
        self.rss = rss
        self.ytdlp = ytdlp

    # -- the steps, each usable on its own -----------------------------------

    def from_data_api(
        self,
        channel_id: str | None = None,
        handle: str | None = None,
        username: str | None = None,
    ) -> list[VideoListing]:
        """Spec 8.1 step 1: the official API, which needs a key."""
        if self.data_api is None:
            raise ListingFailed("the official API is not configured: no API key was given")
        videos = self.data_api.list_channel_videos(  # type: ignore[attr-defined]
            channel_id=channel_id, handle=handle, username=username
        )
        return _require_videos(videos, "the official API")

    def from_rss(self, channel_id: str | None) -> list[VideoListing]:
        """Spec 8.1 step 2: the public feed, which needs a channel id."""
        if self.rss is None:
            raise ListingFailed("the public feed reader is not configured")
        if not channel_id:
            raise ListingFailed("the public feed needs a channel id, which is not known yet")
        videos = self.rss.list_videos(channel_id)  # type: ignore[attr-defined]
        return _require_videos(videos, "the public feed")

    def from_ytdlp_flat(self, channel_url: str | None) -> list[VideoListing]:
        """Spec 8.1 step 4: yt-dlp, which needs the channel's page."""
        if self.ytdlp is None:
            raise ListingFailed("the yt-dlp reader is not configured")
        if not channel_url:
            raise ListingFailed("yt-dlp needs a channel URL, which is not known yet")
        videos = self.ytdlp.list_videos(channel_url)  # type: ignore[attr-defined]
        return _require_videos(videos, "yt-dlp")

    # TODO(unit D): spec 8.1 step 3, the channel tab HTML, goes between the
    # feed and yt-dlp, as from_channel_html(channel_url). It is not this
    # unit's work and nothing here pretends to read a page.

    # -- the ladder ----------------------------------------------------------

    def list_videos(
        self,
        *,
        channel_id: str | None = None,
        handle: str | None = None,
        username: str | None = None,
        channel_url: str | None = None,
    ) -> LadderResult:
        """Try each step until one answers with videos."""
        if not any((channel_id, handle, username, channel_url)):
            raise ValueError("a channel id, a handle, a username, or a channel URL is required")
        url = channel_url or (
            YOUTUBE_CHANNEL_URL.format(channel_id=channel_id) if channel_id else None
        )

        steps = (
            (SOURCE_DATA_API, lambda: self.from_data_api(channel_id, handle, username)),
            (SOURCE_RSS, lambda: self.from_rss(channel_id)),
            (SOURCE_YTDLP, lambda: self.from_ytdlp_flat(url)),
        )

        attempts: list[LadderAttempt] = []
        for method, step in steps:
            try:
                videos = step()
            except VideoError as exc:
                attempts.append(
                    LadderAttempt(
                        method=method,
                        label=source_label(method),
                        ok=False,
                        reason=str(exc),
                        count=0,
                        error=exc,
                    )
                )
                continue
            attempts.append(
                LadderAttempt(
                    method=method,
                    label=source_label(method),
                    ok=True,
                    count=len(videos),
                )
            )
            return LadderResult(
                method=method,
                label=source_label(method),
                videos=tuple(videos),
                attempts=tuple(attempts),
            )

        raise NoVideosFound(attempts)
