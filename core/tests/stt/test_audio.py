"""Downloading the audio, and why (spec 8.3, 8.5, 8.10).

Audio is downloaded for three reasons and no others. These tests hold the set
to the three the spec names, and hold the two things that are not reasons out
of it: a rate limit is a paced retry (spec 8.3), and a reason nobody wrote down
is not a reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from townrecord.runtime.javascript import FALLBACK_RUNTIME
from townrecord.stt import (
    AUDIO_OUTPUT_TEMPLATE,
    AUDIO_TRIGGER_REASONS,
    AudioRequest,
    AudioRequestError,
    AudioTrigger,
    NotAnAudioTrigger,
    audio_download_argv,
)

WATCH_URL = "https://www.youtube.com/watch?v=rLj2Y6L1c9o"
WORK_DIR = Path("capture") / "v-0001"
ARCHIVE_FILE = Path("capture") / "downloaded.txt"


def test_the_audio_download_command_is_the_spec_command() -> None:
    """Spec 8.5's command, argument for argument, and never one string."""
    argv = audio_download_argv(WATCH_URL, WORK_DIR, ARCHIVE_FILE)
    assert argv == [
        sys.executable,
        "-m",
        "yt_dlp",
        "-x",
        "--audio-format",
        "opus",
        "--audio-quality",
        "5",
        "--write-info-json",
        "--js-runtimes",
        "node",
        "--sleep-requests",
        "1",
        "--download-archive",
        str(ARCHIVE_FILE),
        "-o",
        str(WORK_DIR / AUDIO_OUTPUT_TEMPLATE),
        WATCH_URL,
    ]
    assert all(isinstance(part, str) for part in argv)


def test_the_audio_download_is_given_the_runtime_the_job_resolved() -> None:
    """Spec 8.3, 8.9: the runtime of the private venv reaches this command too.

    The fallback of the plain command above is the bare word ``node``, which is
    what a machine that has never installed a private runtime gets.
    """
    argv = audio_download_argv(
        WATCH_URL, WORK_DIR, ARCHIVE_FILE, js_runtime="deno:/opt/venv/bin/deno"
    )
    assert argv[argv.index("--js-runtimes") + 1] == "deno:/opt/venv/bin/deno"

    request = AudioRequest(watch_url=WATCH_URL, trigger=AudioTrigger.NO_CAPTIONS)
    assert request.argv(WORK_DIR, ARCHIVE_FILE, js_runtime="deno:/opt/venv/bin/deno") == argv


def test_an_audio_download_with_no_runtime_named_gets_the_bare_fallback() -> None:
    """A machine with no private runtime yet still gets a command built."""
    argv = audio_download_argv(WATCH_URL, WORK_DIR, ARCHIVE_FILE)
    assert argv[argv.index("--js-runtimes") + 1] == FALLBACK_RUNTIME


def test_a_reason_is_one_of_the_three_the_spec_names() -> None:
    """The closed set of spec 8.5 and 8.10, in the order they are ordered."""
    assert tuple(AUDIO_TRIGGER_REASONS) == tuple(AudioTrigger)
    assert {member.value for member in AudioTrigger} == {
        "no_captions",
        "captions_unusable",
        "local_transcription_only",
    }


@pytest.mark.parametrize(
    "value", ["http_429", "429", "rate_limited", "too_many_requests", "throttled", ""]
)
def test_a_rate_limit_is_not_a_reason_to_download_audio(value: str) -> None:
    """Spec 8.3: 429 is a paced retry, so it can never be a trigger.

    The value is refused by name, and it cannot be spelled into a request
    either, because a request's trigger goes through the same reader.
    """
    with pytest.raises(NotAnAudioTrigger) as refusal:
        AudioTrigger.from_value(value)
    assert value in str(refusal.value)

    with pytest.raises(NotAnAudioTrigger):
        AudioRequest(watch_url=WATCH_URL, trigger=value, detail="rate limited")


def test_an_audio_request_cannot_be_built_without_a_reason() -> None:
    """The reason is not optional and has no default, so it is always stored."""
    with pytest.raises(TypeError):
        AudioRequest(watch_url=WATCH_URL)  # type: ignore[call-arg]


def test_an_audio_request_carries_its_reason_to_the_command() -> None:
    """One place builds the command, and a request uses it."""
    request = AudioRequest(
        watch_url=WATCH_URL,
        trigger=AudioTrigger.CAPTIONS_UNUSABLE,
        detail="the caption track is three minutes short.",
    )
    assert request.trigger is AudioTrigger.CAPTIONS_UNUSABLE
    assert request.argv(WORK_DIR, ARCHIVE_FILE) == audio_download_argv(
        WATCH_URL, WORK_DIR, ARCHIVE_FILE
    )


def test_a_trigger_read_back_from_stored_text_is_the_member() -> None:
    """A job payload holds the value, not the member."""
    assert AudioTrigger.from_value("no_captions") is AudioTrigger.NO_CAPTIONS


def test_an_audio_request_needs_the_watch_url() -> None:
    with pytest.raises(AudioRequestError):
        AudioRequest(watch_url="   ", trigger=AudioTrigger.NO_CAPTIONS)
