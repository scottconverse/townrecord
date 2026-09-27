"""The yt-dlp flat listing method (spec 8.1 step 4).

The command is built as an argument list and run with an allow-listed
environment and no shell (spec 8.3, PROJECT-BRIEF rule E). No test runs
yt-dlp: the runner is injected.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

from townrecord import proc
from townrecord.video.youtube.listing import SOURCE_YTDLP, VideoError, VideoListing
from townrecord.video.youtube.ytdlp import (
    JS_RUNTIME,
    YTDLP_MODULE,
    YTDLP_PLAYLIST_END,
    YTDLP_TABS,
    YTDLP_TIMEOUT_S,
    YtdlpFlatLister,
    video_from_info,
    ytdlp_argv,
)

from .conftest import CITY_CHANNEL_ID, CITY_UPLOADS_PLAYLIST, FakeRunner

CHANNEL_URL = f"https://www.youtube.com/channel/{CITY_CHANNEL_ID}"


def listing_bytes(entries: list[object], **extra: object) -> bytes:
    """A hand-written flat listing, in the shape yt-dlp prints."""
    payload = {"_type": "playlist", "channel_id": CITY_CHANNEL_ID, "entries": entries}
    payload.update(extra)
    return json.dumps(payload).encode("utf-8")


def an_entry(video_id: str, title: str, live_status: str = "was_live") -> dict[str, object]:
    return {
        "id": video_id,
        "title": title,
        "live_status": live_status,
        "duration": 60,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "__x_forwarded_for_ip": "203.0.113.7",
    }


def test_the_command_is_the_documented_one() -> None:
    argv = ytdlp_argv("/usr/bin/python", CHANNEL_URL, "/streams")

    assert argv == [
        "/usr/bin/python",
        "-m",
        "yt_dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--playlist-end",
        "50",
        "--js-runtimes",
        "node",
        f"{CHANNEL_URL}/streams",
    ]
    assert isinstance(argv, list), "an argument list, never one string for a shell"
    assert YTDLP_MODULE == "yt_dlp"
    assert JS_RUNTIME == "node"
    assert YTDLP_PLAYLIST_END == 50
    assert YTDLP_TABS == ("/streams", "/videos")
    assert YTDLP_TIMEOUT_S == 60


def test_a_trailing_slash_does_not_double() -> None:
    argv = ytdlp_argv("python", CHANNEL_URL + "/", "/videos")

    assert argv[-1] == f"{CHANNEL_URL}/videos"


def test_the_private_python_runtime_is_the_default() -> None:
    """Spec 8.9: TownRecord manages its own Python and its own yt-dlp."""
    assert YtdlpFlatLister(runner=FakeRunner()).interpreter == sys.executable


def test_the_recorded_flat_listing_parses(flat_bytes: bytes, runner: FakeRunner) -> None:
    runner.answers["streams"] = (flat_bytes, 0, "")

    videos = YtdlpFlatLister(runner=runner).list_videos(CHANNEL_URL)

    assert len(videos) == 10
    assert videos[0] == VideoListing(
        video_id="7WmcO8mBaCk",
        title="City Council Regular Session - 15 December 2026",
        published=None,
        channel_id=CITY_CHANNEL_ID,
        source_method=SOURCE_YTDLP,
        duration_s=None,
        live_broadcast_content=None,
        live_status="is_upcoming",
    )
    assert videos[0].readiness() == "upcoming"
    assert videos[-1].video_id == "DDrMAVD9N34"
    assert videos[-1].duration_s == 1551
    assert videos[-1].readiness() == "finished"


def test_the_signed_request_field_is_not_kept(flat_bytes: bytes, runner: FakeRunner) -> None:
    """`__x_forwarded_for_ip` is the caller's IP. It is never stored."""
    runner.answers["streams"] = (flat_bytes, 0, "")

    videos = YtdlpFlatLister(runner=runner).list_videos(CHANNEL_URL)

    assert "__x_forwarded_for_ip" not in {field.name for field in dataclasses.fields(VideoListing)}
    assert "__x_forwarded_for_ip" not in repr(videos)


def test_the_runner_gets_a_list_a_timeout_and_an_allow_listed_environment(
    flat_bytes: bytes, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    runner.answers["streams"] = (flat_bytes, 0, "")

    YtdlpFlatLister(runner=runner).list_videos(CHANNEL_URL)

    call = runner.calls[0]
    assert isinstance(call["argv"], list)
    assert call["timeout_s"] == 60
    assert "OPENAI_API_KEY" not in call["env"]
    assert call["env"] == proc.allowed_environment()
    assert "PATH" in {name.upper() for name in call["env"]}


def test_streams_is_tried_before_videos(runner: FakeRunner) -> None:
    runner.answers["streams"] = (listing_bytes([an_entry("aaa", "One")]), 0, "")
    runner.answers["videos"] = (listing_bytes([an_entry("bbb", "Two")]), 0, "")

    videos = YtdlpFlatLister(runner=runner).list_videos(CHANNEL_URL)

    assert [video.video_id for video in videos] == ["aaa"]
    assert len(runner.calls) == 1
    assert runner.calls[0]["argv"][-1].endswith("/streams")


def test_videos_is_tried_when_streams_is_empty(runner: FakeRunner) -> None:
    runner.answers["streams"] = (listing_bytes([]), 0, "")
    runner.answers["videos"] = (listing_bytes([an_entry("bbb", "Two")]), 0, "")

    videos = YtdlpFlatLister(runner=runner).list_videos(CHANNEL_URL)

    assert [video.video_id for video in videos] == ["bbb"]
    assert [call["argv"][-1] for call in runner.calls] == [
        f"{CHANNEL_URL}/streams",
        f"{CHANNEL_URL}/videos",
    ]


def test_zero_from_both_tabs_is_a_failure_with_both_reasons(runner: FakeRunner) -> None:
    runner.answers["streams"] = (listing_bytes([]), 0, "")
    runner.answers["videos"] = (listing_bytes([]), 0, "")

    with pytest.raises(VideoError) as caught:
        YtdlpFlatLister(runner=runner).list_videos(CHANNEL_URL)

    reason = str(caught.value)
    assert "0 videos" in reason
    assert "/streams" in reason and "/videos" in reason


def test_a_failed_command_is_a_failure_with_its_stderr(runner: FakeRunner) -> None:
    runner.answers["streams"] = (b"", 1, "ERROR: unable to download webpage")

    with pytest.raises(VideoError) as caught:
        YtdlpFlatLister(runner=runner).list_videos(CHANNEL_URL)

    assert "unable to download webpage" in str(caught.value)
    assert "1" in str(caught.value)


def test_output_that_is_not_json_is_a_failure(runner: FakeRunner) -> None:
    runner.answers["streams"] = (b"<html>consent</html>", 0, "")

    with pytest.raises(VideoError) as caught:
        YtdlpFlatLister(runner=runner).list_videos(CHANNEL_URL)

    assert "JSON" in str(caught.value)


def test_output_without_entries_is_a_failure(runner: FakeRunner) -> None:
    runner.answers["streams"] = (json.dumps({"title": "one video"}).encode(), 0, "")

    with pytest.raises(VideoError):
        YtdlpFlatLister(runner=runner).list_videos(CHANNEL_URL)


def test_a_timeout_is_reported_with_the_limit(runner: FakeRunner) -> None:
    runner.raises = proc.ProcessTimedOut(("python", "-m", "yt_dlp"), 60.0)

    with pytest.raises(VideoError) as caught:
        YtdlpFlatLister(runner=runner).list_videos(CHANNEL_URL)

    assert "60" in str(caught.value)

    # The timeout is a failure, not a silent zero.
    assert runner.calls


def test_the_flat_listing_keeps_the_playlist_channel_id(
    flat_bytes: bytes, runner: FakeRunner
) -> None:
    """Flat entries carry no channel id of their own; the playlist does."""
    runner.answers["streams"] = (flat_bytes, 0, "")

    videos = YtdlpFlatLister(runner=runner).list_videos(CHANNEL_URL)

    assert {video.channel_id for video in videos} == {CITY_CHANNEL_ID}
    assert CITY_UPLOADS_PLAYLIST.startswith("UU"), "an uploads playlist, for the API path"


def test_an_info_json_gives_readiness(video_info: dict[str, object]) -> None:
    """Spec 8.2 third path: yt-dlp's own live_status for one known video."""
    listing = video_from_info(video_info)

    assert listing == VideoListing(
        video_id="3qfQAkAAC9U",
        title="City Council Regular Session - 08 September 2026",
        published="20260910",
        channel_id=CITY_CHANNEL_ID,
        source_method=SOURCE_YTDLP,
        duration_s=14407,
        live_status="not_live",
    )
    assert listing.readiness() == "finished"


def test_the_recorded_info_fixture_is_the_tiny_hand_written_one() -> None:
    """The captured info.json holds signed media URLs. It is not in the repo."""
    path = Path(__file__).parent / "fixtures" / "video-info-3qfQAkAAC9U.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert "formats" not in payload
    assert "automatic_captions" not in payload
    assert payload["live_status"] == "not_live"
    assert payload["duration"] == 14407
