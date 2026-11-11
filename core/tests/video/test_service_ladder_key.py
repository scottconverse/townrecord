"""The service's listing ladder turns on spec 8.1 step 1 when a key loads.

Fake keys only, and no network: the API and the feed answer through a mock
transport, and yt-dlp is a fake that lists nothing.
"""

from __future__ import annotations

import httpx
import pytest

from townrecord.video.watch import service_ladder
from townrecord.video.youtube.data_api import API_KEY_HEADER
from townrecord.video.youtube.listing import SOURCE_DATA_API

from .conftest import CITY_CHANNEL_ID, TEST_API_KEY, FakeDataApi, offline_client

KEY_ENV = "TOWNRECORD_YOUTUBE_API_KEY"


class NoVideos:
    """A yt-dlp lister that lists nothing, so no test starts a process."""

    def list_videos(self, channel_url: str) -> list:
        return []


def test_a_key_from_the_environment_turns_on_the_official_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a key, the ladder answers from the official API, before the feed."""
    monkeypatch.setenv(KEY_ENV, TEST_API_KEY)
    api = FakeDataApi()
    client = offline_client(transport=httpx.MockTransport(api.handle))

    result = service_ladder(client=client, ytdlp=NoVideos()).list_videos(channel_id=CITY_CHANNEL_ID)

    assert result.method == SOURCE_DATA_API
    assert [path.rsplit("/", 1)[-1] for path in api.paths()] == [
        "channels",
        "playlistItems",
        "videos",
    ]
    for request in api.requests:
        assert request.headers[API_KEY_HEADER] == TEST_API_KEY
        assert TEST_API_KEY not in str(request.url)


def test_no_key_keeps_the_not_configured_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a key the step is skipped and the checkpoint says why."""
    from townrecord import credentials

    monkeypatch.delenv(KEY_ENV, raising=False)
    monkeypatch.setattr(credentials, "read_credential", lambda target: None)
    feed = httpx.MockTransport(lambda request: httpx.Response(404))

    ladder = service_ladder(client=offline_client(transport=feed), ytdlp=NoVideos())

    assert ladder.data_api is None
    with pytest.raises(Exception):  # noqa: B017 -- every step fails here
        ladder.list_videos(channel_id=CITY_CHANNEL_ID)
    with pytest.raises(Exception, match="no API key was given"):
        ladder.from_data_api(CITY_CHANNEL_ID)
