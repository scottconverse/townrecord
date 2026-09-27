"""Readiness: is the meeting upcoming, live, finished, or not yet known (spec 8.2)?

The spec gives the order. First the official Data API: its
``liveBroadcastContent`` with the video's length. Then yt-dlp's
``live_status``. A third path, the player endpoint, needs the network and is
not built here.

``unknown`` is not a failure. It means "waiting for status metadata (will
retry)", which is the honest answer when nothing has said anything yet: a
public feed carries no status at all, and a premiere has a length before it
has played.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

READINESS_UPCOMING = "upcoming"
READINESS_LIVE = "live"
READINESS_FINISHED = "finished"
READINESS_UNKNOWN = "unknown"

#: The four words, in the order the spec lists them.
READINESS_VALUES: tuple[str, ...] = (
    READINESS_UPCOMING,
    READINESS_LIVE,
    READINESS_FINISHED,
    READINESS_UNKNOWN,
)

#: What the Data API's `liveBroadcastContent` says on its own.
LIVE_BROADCAST_READINESS: dict[str, str] = {
    "live": READINESS_LIVE,
    "upcoming": READINESS_UPCOMING,
}

#: What yt-dlp's `live_status` says.
#:
#: `post_live` is the judgment call: the broadcast has ended but the
#: recording is still being written, so a download started now would find
#: nothing. It waits instead, and the reason says so.
LIVE_STATUS_READINESS: dict[str, str] = {
    "is_upcoming": READINESS_UPCOMING,
    "is_live": READINESS_LIVE,
    "was_live": READINESS_FINISHED,
    "not_live": READINESS_FINISHED,
    "post_live": READINESS_UNKNOWN,
}

#: The words used whenever the answer is "not yet".
READINESS_WAITING_REASON = "waiting for status metadata (will retry)"


def _from_broadcast_content(content: Any, duration_s: Any) -> tuple[str, str] | None:
    """The Data API's answer, or ``None`` when it cannot decide.

    ``liveBroadcastContent == "none"`` with a length means the recording is
    done. The same word with no length is not a verdict: the video exists but
    nothing has been recorded yet.
    """
    if content is None:
        return None
    if isinstance(content, str) and content in LIVE_BROADCAST_READINESS:
        return (
            LIVE_BROADCAST_READINESS[content],
            f"the official API says liveBroadcastContent={content}",
        )
    if content == "none":
        if isinstance(duration_s, int) and not isinstance(duration_s, bool) and duration_s > 0:
            return (
                READINESS_FINISHED,
                "the official API says liveBroadcastContent=none and gives"
                f" a length of {duration_s} seconds, so the recording is done",
            )
        return None
    return (
        READINESS_UNKNOWN,
        f"the official API says liveBroadcastContent={content!r}, which this reader does not know",
    )


def _from_live_status(status: Any) -> tuple[str, str] | None:
    """yt-dlp's answer, or ``None`` when it has nothing to say."""
    if not isinstance(status, str) or not status:
        return None
    if status == "post_live":
        return (
            READINESS_UNKNOWN,
            "yt-dlp says live_status=post_live: the stream has ended and the"
            f" recording is still being made, so this {READINESS_WAITING_REASON}",
        )
    known = LIVE_STATUS_READINESS.get(status)
    if known is None:
        return (
            READINESS_UNKNOWN,
            f"yt-dlp says live_status={status!r}, which this reader does not know",
        )
    return (known, f"yt-dlp says live_status={status}")


def _sources(subject: object | None, info: Mapping[str, Any] | None) -> tuple[Any, Any, Any]:
    """Read the three status fields off a listing and/or a yt-dlp info dict.

    A listing wins over an info dict for any field it already carries.
    """
    content = getattr(subject, "live_broadcast_content", None)
    duration_s = getattr(subject, "duration_s", None)
    status = getattr(subject, "live_status", None)
    if info is not None:
        if content is None:
            content = info.get("liveBroadcastContent")
        if duration_s is None:
            duration_s = info.get("duration")
        if status is None:
            status = info.get("live_status")
    return content, duration_s, status


def _verdict(subject: object | None, info: Mapping[str, Any] | None) -> tuple[str, str]:
    """The readiness word and the plain reason for it."""
    content, duration_s, status = _sources(subject, info)
    waiting_reason = READINESS_WAITING_REASON
    for source in (_from_broadcast_content(content, duration_s), _from_live_status(status)):
        if source is None:
            continue
        outcome, reason = source
        if outcome != READINESS_UNKNOWN:
            return (outcome, reason)
        waiting_reason = reason
    return (READINESS_UNKNOWN, waiting_reason)


def readiness(subject: object | None = None, *, info: Mapping[str, Any] | None = None) -> str:
    """One of :data:`READINESS_VALUES` for a listing, a yt-dlp info dict, or both."""
    return _verdict(subject, info)[0]


def readiness_reason(
    subject: object | None = None, *, info: Mapping[str, Any] | None = None
) -> str:
    """Why :func:`readiness` answered what it did, in plain words."""
    return _verdict(subject, info)[1]
