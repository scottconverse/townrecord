"""Shared pieces for the YouTube video tests.

No test reaches the network. Every reply is either a recorded fixture from
`evidence/fixtures/youtube/` (copied into `fixtures/`) or is hand-written
here. The YouTube Data API runs over `httpx.MockTransport`, the yt-dlp
listing runs over an injected fake runner, which is why both take their
transport and their runner as arguments.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from townrecord import proc

FIXTURE_FOLDER = Path(__file__).parent / "fixtures"

#: The City of Longmont channel (confirmed September 27, 2026, spec 7.2 step 3).
CITY_CHANNEL_ID = "UCH5_wkpLrKYb1JuUk6-UdNg"

#: The Longmont Public Media channel. Two channels record the same meetings.
PUBLIC_MEDIA_CHANNEL_ID = "UCXFW3IRzfCc6q_XM-uutAmw"

#: The uploads playlist of the City channel.
CITY_UPLOADS_PLAYLIST = "UUH5_wkpLrKYb1JuUk6-UdNg"

#: A test handle, so nothing here suggests a real city.
TEST_HANDLE = "@testchannel"

#: The API key of the tests. It must never appear in a URL.
TEST_API_KEY = "test-api-key-not-real"

#: A fixed clock, so quota days are deterministic.
FIXED_NOW = datetime(2026, 9, 27, 20, 30, tzinfo=UTC)


def offline_client(**kwargs: Any) -> httpx.Client:
    """An httpx.Client that ignores the machine's proxy settings.

    A test must not inherit HTTP_PROXY or ALL_PROXY from whoever runs it:
    on a machine whose proxy points at a closed port, httpx refuses to build
    the client at all, and the reading is about a fixture, not a proxy.
    """
    return httpx.Client(trust_env=False, **kwargs)


def fixture_bytes(name: str) -> bytes:
    """Return the bytes of one recorded fixture."""
    return (FIXTURE_FOLDER / name).read_bytes()


def fixture_json(name: str) -> Any:
    """Return the parsed JSON of one recorded fixture."""
    return json.loads(fixture_bytes(name))


@pytest.fixture
def city_rss() -> bytes:
    """The Channel RSS feed of the City of Longmont channel."""
    return fixture_bytes("rss-UCH5_wkpLrKYb1JuUk6-UdNg.xml")


@pytest.fixture
def public_media_rss() -> bytes:
    """The Channel RSS feed of the Longmont Public Media channel."""
    return fixture_bytes("rss-UCXFW3IRzfCc6q_XM-uutAmw.xml")


@pytest.fixture
def flat_entries() -> Any:
    """The trimmed yt-dlp flat listing: 10 entries, both live statuses."""
    return fixture_json("flat-UCH5-streams-10.json")


@pytest.fixture
def flat_bytes() -> bytes:
    """The raw bytes of the trimmed yt-dlp flat listing."""
    return fixture_bytes("flat-UCH5-streams-10.json")


@pytest.fixture
def video_info() -> Any:
    """The hand-written tiny info.json: live_status not_live, duration 14407."""
    return fixture_json("video-info-3qfQAkAAC9U.json")


class FakeDataApi:
    """A fake YouTube Data API that records every request it answers.

    The three replies are hand-written, in the shape the API returns.
    """

    def __init__(self) -> None:
        self.api_key = TEST_API_KEY
        self.requests: list[httpx.Request] = []
        #: Flip to test a hard failure or a quota refusal.
        self.channels_status = 200
        self.channels_body: Any = self._channel_body()
        self.playlist_body: Any = self._playlist_body()
        self.videos_body: Any = self._videos_body()

    # -- the pieces a test asks for ------------------------------------------

    def client(self, **kwargs: Any) -> httpx.Client:
        from townrecord.video.youtube.data_api import DataApiClient

        return DataApiClient(
            self.api_key,
            offline_client(transport=httpx.MockTransport(self.handle)),
            **kwargs,
        )

    def paths(self) -> list[str]:
        """The API paths asked for, in order."""
        return [request.url.path for request in self.requests]

    def last_params(self) -> Mapping[str, str]:
        return self.requests[-1].url.params if self.requests else {}

    # -- hand-written replies ------------------------------------------------

    def _channel_body(self, *_: Any) -> Any:
        return {
            "items": [
                {
                    "id": CITY_CHANNEL_ID,
                    "snippet": {"title": "Test channel", "channelId": CITY_CHANNEL_ID},
                    "contentDetails": {"relatedPlaylists": {"uploads": CITY_UPLOADS_PLAYLIST}},
                }
            ]
        }

    def _playlist_body(self) -> Any:
        return {
            "items": [
                self._playlist_item(
                    "jhsFsEz0P5A",
                    "City Council Regular Session - 22 September 2026",
                    "2026-09-23T18:53:52Z",
                ),
                self._playlist_item(
                    "DUzTpP66lk0",
                    "2026 LevelUp Longmont Esports Tournament",
                    "2026-09-20T00:33:16Z",
                ),
                self._playlist_item(
                    "uHFuCfd5MVQ",
                    "City Council Regular Session - 20 October 2026",
                    "2026-10-19T18:00:00Z",
                ),
            ]
        }

    def _playlist_item(self, video_id: str, title: str, published: str) -> Any:
        return {
            "contentDetails": {"videoId": video_id, "videoPublishedAt": published},
            "snippet": {
                "title": title,
                "channelId": CITY_CHANNEL_ID,
                "publishedAt": published,
            },
        }

    def _videos_body(self) -> Any:
        return {
            "items": [
                self._video("jhsFsEz0P5A", "none", "PT3H58M10S"),
                self._video("DUzTpP66lk0", "none", "PT6H15M1S"),
                self._video("uHFuCfd5MVQ", "upcoming", "P0D"),
            ]
        }

    def _video(self, video_id: str, live_broadcast_content: str, duration: str) -> Any:
        return {
            "id": video_id,
            "snippet": {
                "title": f"Video {video_id}",
                "channelId": CITY_CHANNEL_ID,
                "liveBroadcastContent": live_broadcast_content,
            },
            "contentDetails": {"duration": duration},
            "status": {"uploadStatus": "processed"},
        }

    # -- the transport -------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/channels"):
            return httpx.Response(self.channels_status, json=self.channels_body)
        if path.endswith("/playlistItems"):
            return httpx.Response(200, json=self.playlist_body)
        if path.endswith("/videos"):
            return httpx.Response(200, json=self.videos_body)
        if path.endswith("/search"):
            return httpx.Response(200, json={"items": []})
        return httpx.Response(404, text=f"the fake API has no {path}")


@pytest.fixture
def api() -> FakeDataApi:
    """A fake YouTube Data API."""
    return FakeDataApi()


class FakeRunner:
    """A fake child-process runner: it answers from a script and records calls."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        #: argv suffix (the tab) mapping to (stdout bytes, returncode, stderr).
        self.answers: dict[str, tuple[bytes, int, str]] = {}
        #: Set to raise, to prove a timeout is reported and not swallowed.
        self.raises: Exception | None = None

    def __call__(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float,
        env: Mapping[str, str],
        cwd: str | None = None,
    ) -> proc.ProcessResult:
        self.calls.append(
            {"argv": list(argv), "timeout_s": timeout_s, "env": dict(env), "cwd": cwd}
        )
        if self.raises is not None:
            raise self.raises
        suffix = str(argv[-1]).rsplit("/", 1)[-1]
        stdout, returncode, stderr = self.answers.get(suffix, (b"{}", 0, ""))
        return proc.ProcessResult(tuple(str(part) for part in argv), returncode, stdout, stderr)


@pytest.fixture
def runner() -> FakeRunner:
    """A fake yt-dlp runner."""
    return FakeRunner()
