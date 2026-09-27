"""The one listing shape every YouTube method answers with (spec 8.1).

Each ladder step reads a channel its own way but hands back the same frozen
dataclass, so the step after it does not care which method answered. The
status fields are optional on purpose: the official API knows them, a public
feed does not, and yt-dlp usually does. :mod:`townrecord.video.youtube.readiness`
turns whatever is there into a readiness word.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from townrecord.adapters.base import AdapterError
from townrecord.video.youtube.readiness import readiness, readiness_reason

#: Spec 8.1 step 1: the official Data API.
SOURCE_DATA_API = "data_api"
#: Spec 8.1 step 2: the channel's public feed.
SOURCE_RSS = "rss"
#: Spec 8.1 step 4: yt-dlp reading the channel's tabs.
SOURCE_YTDLP = "ytdlp"

#: Spec 8.1: the interface says how the channel was read, in these words.
SOURCE_LABELS: dict[str, str] = {
    SOURCE_DATA_API: "Read YouTube with the official API",
    SOURCE_RSS: "Read the public feed instead",
    SOURCE_YTDLP: "Read the public channel listing with yt-dlp",
}


def source_label(method: str) -> str:
    """The words the interface shows for a reading method."""
    return SOURCE_LABELS.get(method, f"Read YouTube with {method}")


class VideoError(AdapterError):
    """A YouTube read could not be done, with a plain reason attached."""


class ListingFailed(VideoError):
    """One method tried and failed, with its reason. The ladder carries on."""


class QuotaBlocked(ListingFailed):
    """The Data API's daily quota is used up; the block holds until Pacific midnight."""


@dataclass(frozen=True)
class VideoListing:
    """One video, as a listing method saw it.

    ``published`` is the time exactly as the source gave it: the Data API
    answers an ISO timestamp, yt-dlp's flat listing answers a date, a feed
    answers an ISO timestamp. It is not rewritten here.
    """

    video_id: str
    title: str
    published: str | None
    channel_id: str
    source_method: str
    duration_s: int | None = None
    live_broadcast_content: str | None = None
    live_status: str | None = None

    @property
    def label(self) -> str:
        """The words the interface shows for how this was read."""
        return source_label(self.source_method)

    def readiness(self) -> str:
        """upcoming | live | finished | unknown (spec 8.2). Never a failure."""
        return readiness(self)

    def readiness_reason(self) -> str:
        """Why the readiness word is that one, in plain words."""
        return readiness_reason(self)


class NoVideosFound(VideoError):
    """Every method came back with nothing. Zero is a failure, never a quiet day.

    The failure carries each method's own reason, so the report says which
    reader failed and why: a used up quota reads differently from a channel
    that really has posted nothing.
    """

    def __init__(self, attempts: Sequence[object]) -> None:
        self.attempts = tuple(attempts)
        reasons = "; ".join(
            f"{getattr(attempt, 'method', '?')}: {getattr(attempt, 'reason', 'no reason')}"
            for attempt in self.attempts
        )
        super().__init__(f"no method listed any videos for this channel. {reasons}")


@dataclass(frozen=True)
class LadderAttempt:
    """What one ladder step did, for the report the interface shows."""

    method: str
    label: str
    ok: bool
    reason: str = ""
    count: int = 0
    error: BaseException | None = field(default=None, compare=False, repr=False)
