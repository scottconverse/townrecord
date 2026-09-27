"""yt-dlp reading a channel's tabs (spec 8.1 step 4).

Last in the ladder, it needs no key and no quota, and it can see a live
broadcast while it is happening. It is also the slowest and the most likely
to be asked to prove it is not a robot, so it runs under a time limit and its
output is read as a flat listing, never as media.

The command is built as an argument list and run with an allow-listed
environment and no shell (spec 8.3, PROJECT-BRIEF rule E): see
:mod:`townrecord.proc`. The runner is injected, so no test starts yt-dlp.

Spec 8.9: TownRecord manages its own Python runtime, so the interpreter
defaults to the one running this code rather than to whatever ``yt-dlp``
happens to be on the user's PATH.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from typing import Any

from townrecord import proc
from townrecord.video.youtube.listing import (
    SOURCE_YTDLP,
    ListingFailed,
    VideoError,
    VideoListing,
)

#: The module, not the script: the private runtime is asked for it by name.
YTDLP_MODULE = "yt_dlp"

#: Spec 8.1: the newest fifty items.
YTDLP_PLAYLIST_END = 50

#: YouTube's pages need a JavaScript runtime. Node is what the tool is told to use.
JS_RUNTIME = "node"

#: The two channel tabs, in the order they are tried.
YTDLP_TABS: tuple[str, ...] = ("/streams", "/videos")

#: Seconds. A listing is a small read; this is not a download.
YTDLP_TIMEOUT_S = 60

#: The shape of an injected runner, matching :func:`townrecord.proc.run`.
Runner = Callable[..., proc.ProcessResult]


def ytdlp_argv(interpreter: str, channel_url: str, tab: str) -> list[str]:
    """The exact command, as an argument list for :mod:`subprocess`."""
    base = channel_url.rstrip("/")
    return [
        interpreter,
        "-m",
        YTDLP_MODULE,
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end",
        str(YTDLP_PLAYLIST_END),
        "--js-runtimes",
        JS_RUNTIME,
        f"{base}{tab}",
    ]


def _duration(entry: Mapping[str, Any]) -> int | None:
    value = entry.get("duration")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _published(entry: Mapping[str, Any]) -> str | None:
    """yt-dlp's own spelling of the time, kept as given (``20260910``)."""
    value = entry.get("upload_date")
    if isinstance(value, str) and value:
        return value
    return None


class YtdlpFlatLister:
    """Reads a channel with yt-dlp, first the streams tab, then the videos tab."""

    def __init__(
        self,
        *,
        runner: Runner = proc.run_allowlisted,
        interpreter: str | None = None,
        timeout_s: float = YTDLP_TIMEOUT_S,
    ) -> None:
        self.runner = runner
        #: Spec 8.9: the Python that runs TownRecord is the Python that runs yt-dlp.
        self.interpreter = interpreter or sys.executable
        self.timeout_s = timeout_s

    def list_videos(self, channel_url: str) -> list[VideoListing]:
        """The channel's newest streams or videos, in one of the two tabs.

        Both tabs failing is a failure that carries both reasons: zero videos
        from yt-dlp is never read as "this channel posts nothing".
        """
        reasons: list[str] = []
        for tab in YTDLP_TABS:
            argv = ytdlp_argv(self.interpreter, channel_url, tab)
            try:
                result = self.runner(argv, timeout_s=self.timeout_s, env=proc.allowed_environment())
            except proc.ProcessTimedOut as exc:
                reasons.append(f"{tab}: yt-dlp was stopped, {exc}")
                continue
            except proc.ProcessFailed as exc:
                reasons.append(f"{tab}: {exc}")
                continue
            except OSError as exc:
                reasons.append(f"{tab}: yt-dlp could not be started: {exc}")
                continue

            if result.returncode != 0:
                detail = result.last_stderr_line() or "no reason given"
                reasons.append(f"{tab}: yt-dlp exited {result.returncode}: {detail}")
                continue
            try:
                payload = json.loads(result.stdout.decode("utf-8", errors="replace"))
            except ValueError as exc:
                reasons.append(f"{tab}: yt-dlp's output was not JSON: {exc}")
                continue
            if not isinstance(payload, Mapping):
                reasons.append(f"{tab}: yt-dlp's output was not a JSON object")
                continue
            try:
                videos = self._from_flat_payload(payload)
            except VideoError as exc:
                reasons.append(f"{tab}: {exc}")
                continue
            if not videos:
                reasons.append(f"{tab}: yt-dlp listed 0 videos")
                continue
            return videos

        raise ListingFailed("the yt-dlp listing found no videos: " + "; ".join(reasons))

    def _from_flat_payload(self, payload: Mapping[str, Any]) -> list[VideoListing]:
        """Flat entries carry no channel id of their own; the playlist does."""
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise VideoError("yt-dlp's flat answer held no entries list")
        channel_id = str(payload.get("channel_id") or "")
        videos: list[VideoListing] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            video_id = str(entry.get("id") or "")
            if not video_id:
                continue
            videos.append(
                VideoListing(
                    video_id=video_id,
                    title=str(entry.get("title") or ""),
                    published=_published(entry),
                    channel_id=channel_id,
                    source_method=SOURCE_YTDLP,
                    duration_s=_duration(entry),
                    live_broadcast_content=None,
                    live_status=entry.get("live_status"),
                )
            )
        return videos


def video_from_info(info: Mapping[str, Any]) -> VideoListing:
    """One video's listing from yt-dlp's own info dict (spec 8.2's third path).

    Only the fields the ladder needs are kept. The captured info dict holds
    signed media URLs and the caller's address; none of that is stored.
    """
    if not isinstance(info, Mapping) or not info.get("id"):
        raise VideoError("the yt-dlp info held no video id")
    return VideoListing(
        video_id=str(info["id"]),
        title=str(info.get("title") or ""),
        published=_published(info),
        channel_id=str(info.get("channel_id") or ""),
        source_method=SOURCE_YTDLP,
        duration_s=_duration(info),
        live_status=info.get("live_status"),
    )
