"""Asking a video's status the way spec 8.2 orders (the capture job's own ask).

Spec 8.2: "Use the API's liveBroadcastContent and duration if available; else
the player endpoint status (LIVE_STREAM_OFFLINE means upcoming); else yt-dlp
--dump-single-json and its live_status field."

The three steps are asked in that order, and the first one that decides ends
the ask. A step that decides nothing says why it did not, so "nothing was
decided" never reads as "nothing was asked". yt-dlp is only ever the injected
runner here, so no test in this file starts a process or reaches the network.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from townrecord import proc
from townrecord.runtime.javascript import FALLBACK_RUNTIME
from townrecord.video.youtube.listing import SOURCE_DATA_API, SOURCE_RSS, SOURCE_YTDLP, VideoListing
from townrecord.video.youtube.readiness import (
    READINESS_FINISHED,
    READINESS_UNKNOWN,
    READINESS_UPCOMING,
)
from townrecord.video.youtube.status import (
    API_NOT_CONFIGURED,
    PLAYER_NOT_BUILT,
    SOURCE_PLAYER,
    STATUS_TIMEOUT_S,
    StatusAsker,
    describe_status,
    info_argv,
    is_finished,
)

from .conftest import CITY_CHANNEL_ID, FakeRunner

#: The video the tests ask about, and the URL the capture command would use.
VIDEO_ID = "vid00000001"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"

#: The suffix the fake runner keys its script on: the last argument's last segment.
TAB = f"watch?v={VIDEO_ID}"


def listing(**overrides: object) -> VideoListing:
    """A listing with only the fields a test cares about set."""
    fields: dict[str, object] = {
        "video_id": VIDEO_ID,
        "title": "City Council Regular Session",
        "published": None,
        "channel_id": CITY_CHANNEL_ID,
        "source_method": SOURCE_RSS,
    }
    fields.update(overrides)
    return VideoListing(**fields)  # type: ignore[arg-type]


class StubApi:
    """Stand-in for the Data API reader of spec 8.2's first step.

    ``answer`` is what one video's read gives back: a listing, None when the API
    holds no such video, or an exception to raise. Every id it was asked for is
    recorded, so a test can prove this step was reached exactly once.
    """

    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def video_status(self, video_id: str) -> Any:
        self.asked.append(video_id)
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def answers(runner: FakeRunner, info: object, *, returncode: int = 0, stderr: str = "") -> None:
    """Script the fake runner's answer to the info ask for :data:`URL`."""
    body = b"" if info is None else json.dumps(info).encode("utf-8")
    runner.answers[TAB] = (body, returncode, stderr)


def asker(runner: FakeRunner, *, api: object | None = None) -> StatusAsker:
    """An asker over the fake runner, with a plain interpreter and runtime."""
    return StatusAsker(runner=runner, interpreter="python", js_runtime="node", api=api)


# -- the command of the third step -------------------------------------------


def test_the_third_step_is_the_command_the_spec_names() -> None:
    assert info_argv("/runtime/python", URL, js_runtime="deno:/runtime/deno") == [
        "/runtime/python",
        "-m",
        "yt_dlp",
        "--dump-single-json",
        "--js-runtimes",
        "deno:/runtime/deno",
        URL,
    ]


def test_the_info_ask_downloads_nothing_so_it_needs_no_skip_download() -> None:
    # The capture command of spec 8.3 carries --skip-download; this one must not,
    # or a reader of a child's argument list could not tell the two apart.
    argv = info_argv("/runtime/python", URL)
    assert "--skip-download" not in argv
    assert argv[-1] == URL


def test_the_javascript_runtime_defaults_to_the_bare_fallback_name() -> None:
    argv = info_argv("/runtime/python", URL)
    assert argv[argv.index("--js-runtimes") + 1] == FALLBACK_RUNTIME


def test_the_ask_uses_the_interpreters_runtime_and_the_allowed_environment(
    runner: FakeRunner,
) -> None:
    asker(runner).ask(video_id=VIDEO_ID, url=URL)
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["argv"] == [
        "python",
        "-m",
        "yt_dlp",
        "--dump-single-json",
        "--js-runtimes",
        "node",
        URL,
    ]
    assert call["timeout_s"] == STATUS_TIMEOUT_S
    # The allow-listed environment and nothing else (spec 8.3, rule E).
    assert set(call["env"]) <= set(proc.ALLOWED_ENV_NAMES)


# -- the order of the steps --------------------------------------------------


def test_the_first_step_that_decides_ends_the_ask(runner: FakeRunner) -> None:
    api = StubApi(listing(live_broadcast_content="none", duration_s=14407))
    answer = asker(runner, api=api).ask(video_id=VIDEO_ID, url=URL)
    assert answer.readiness == READINESS_FINISHED
    assert [ask.method for ask in answer.asks] == [SOURCE_DATA_API]
    assert api.asked == [VIDEO_ID]
    # "else" in the spec: a definite answer means nothing else is asked, and
    # yt-dlp is not started at all.
    assert runner.calls == []
    assert is_finished(answer)


def test_with_no_api_key_the_ask_says_so_and_yt_dlp_is_the_step_that_decides(
    runner: FakeRunner,
) -> None:
    answers(runner, {"id": VIDEO_ID, "live_status": "was_live"})
    answer = asker(runner).ask(video_id=VIDEO_ID, url=URL)
    assert answer.readiness == READINESS_FINISHED
    assert [ask.method for ask in answer.asks] == [SOURCE_DATA_API, SOURCE_PLAYER, SOURCE_YTDLP]
    assert answer.asks[0].reason == API_NOT_CONFIGURED
    assert answer.asks[1].reason == PLAYER_NOT_BUILT
    assert len(runner.calls) == 1
    assert answer.sentence == (
        "Asked: the official API: no API key was given; "
        "the player endpoint status: not built here; "
        "yt-dlp --dump-single-json said finished (yt-dlp says live_status=was_live)."
    )


def test_a_step_that_is_not_built_is_named_rather_than_passed_over_in_silence() -> None:
    # The middle step of the spec is a network call this reader does not make.
    # Its sentence names it, so the reason never reads as though it had run.
    ask = StatusAsker(runner=FakeRunner(), api=None)._ask_player(VIDEO_ID, URL)  # noqa: SLF001
    assert ask.method == SOURCE_PLAYER
    assert ask.readiness == READINESS_UNKNOWN
    assert not ask.answered
    assert ask.reason == PLAYER_NOT_BUILT


@pytest.mark.parametrize(
    ("answer_value", "expected"),
    [
        (None, f"it holds no video {VIDEO_ID}"),
        (listing(), "it decided nothing about this video"),
        (RuntimeError("quota is used up"), "the call failed: quota is used up"),
        (listing(live_broadcast_content="upcoming"), None),
    ],
)
def test_the_api_step_answers_or_says_why_it_did_not(
    runner: FakeRunner, answer_value: object, expected: str | None
) -> None:
    answers(runner, {"id": VIDEO_ID, "live_status": "was_live"})
    answer = asker(runner, api=StubApi(answer_value)).ask(video_id=VIDEO_ID, url=URL)
    first = answer.asks[0]
    assert first.method == SOURCE_DATA_API
    if expected is None:
        assert first.readiness == READINESS_UPCOMING
        assert answer.readiness == READINESS_UPCOMING
        assert runner.calls == []
    else:
        assert first.reason == expected
        # The step decided nothing, so the rest of the spec's order still runs.
        assert answer.readiness == READINESS_FINISHED
        assert [ask.method for ask in answer.asks] == [
            SOURCE_DATA_API,
            SOURCE_PLAYER,
            SOURCE_YTDLP,
        ]


# -- a status that is not an answer ------------------------------------------


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (None, "its output was not JSON: "),
        ([1, 2], "its output was not a JSON object"),
        ({}, "the information carries no live_status"),
        ({"live_status": None}, "the information carries no live_status"),
        ({"live_status": "post_live"}, "live_status=post_live"),
        ({"live_status": "who_knows"}, "live_status=who_knows"),
    ],
)
def test_an_ask_that_decides_nothing_is_unknown_and_says_what_it_answered(
    runner: FakeRunner, info: object, expected: str
) -> None:
    if info is None:
        runner.answers[TAB] = (b"ERROR: nope, this is not JSON", 0, "")
    else:
        answers(runner, info)
    answer = asker(runner).ask(video_id=VIDEO_ID, url=URL)
    assert answer.readiness == READINESS_UNKNOWN
    assert answer.asks[-1].method == SOURCE_YTDLP
    assert answer.asks[-1].reason.startswith(expected)
    assert answer.reason == answer.asks[-1].reason


def test_a_live_video_is_a_definite_answer_not_a_reason_to_keep_asking(
    runner: FakeRunner,
) -> None:
    answers(runner, {"id": VIDEO_ID, "live_status": "is_live"})
    answer = asker(runner).ask(video_id=VIDEO_ID, url=URL)
    assert answer.readiness == "live"
    assert answer.asks[-1].answered
    assert answer.asks[-1].sentence().endswith("(yt-dlp says live_status=is_live)")


def test_a_status_that_stays_unknown_after_the_whole_order_says_what_it_asked(
    runner: FakeRunner,
) -> None:
    answers(runner, {"id": VIDEO_ID, "live_status": "post_live"})
    answer = asker(runner).ask(video_id=VIDEO_ID, url=URL)
    assert answer.readiness == READINESS_UNKNOWN
    assert not is_finished(answer)
    assert answer.sentence.startswith("Asked: ")
    assert answer.sentence.endswith(".")
    assert "the official API: no API key was given" in answer.sentence
    assert "the player endpoint status: not built here" in answer.sentence
    assert "yt-dlp --dump-single-json: live_status=post_live" in answer.sentence
    # The reason is what a deferred job says, and the queue caps a reason at 300
    # characters (jobs/queue.py MAX_REASON): nothing asked may be cut off.
    assert len(answer.reason) < 300


def test_an_ask_that_was_stopped_says_which_program_was_stopped(runner: FakeRunner) -> None:
    runner.raises = proc.ProcessTimedOut(["/runtime/python", "-m", "yt_dlp"], STATUS_TIMEOUT_S)
    answer = asker(runner).ask(video_id=VIDEO_ID, url=URL)
    assert answer.readiness == READINESS_UNKNOWN
    assert answer.reason.startswith("it was stopped, /runtime/python timed out after 60 seconds")


def test_an_ask_that_could_not_be_started_is_unknown_with_the_os_reason(runner: FakeRunner) -> None:
    runner.raises = FileNotFoundError("no such file or directory")
    answer = asker(runner).ask(video_id=VIDEO_ID, url=URL)
    assert answer.readiness == READINESS_UNKNOWN
    assert answer.reason == "it could not be started: no such file or directory"


def test_an_ask_that_exits_nonzero_quotes_yt_dlps_own_line(runner: FakeRunner) -> None:
    answers(
        runner,
        None,
        returncode=1,
        stderr="WARNING: nothing\nERROR: [youtube] vid00000001: this video is not available\n",
    )
    answer = asker(runner).ask(video_id=VIDEO_ID, url=URL)
    assert answer.readiness == READINESS_UNKNOWN
    assert answer.reason == (
        "it exited 1: ERROR: [youtube] vid00000001: this video is not available"
    )


def test_an_ask_that_exits_nonzero_with_no_words_still_says_the_code(runner: FakeRunner) -> None:
    answers(runner, None, returncode=137, stderr="")
    answer = asker(runner).ask(video_id=VIDEO_ID, url=URL)
    assert answer.reason == "it exited 137: no reason given"


# -- the small pieces --------------------------------------------------------


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (
            {"live_status": "post_live"},
            "live_status=post_live: the stream has ended and the recording is still being written",
        ),
        ({"live_status": "was_live"}, "live_status=was_live"),
        ({"live_status": None}, "the information carries no live_status"),
        ({}, "the information carries no live_status"),
    ],
)
def test_describe_status_names_the_field_the_video_carried(
    info: dict[str, Any], expected: str
) -> None:
    assert describe_status(info) == expected
