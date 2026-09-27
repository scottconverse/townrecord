"""The command of spec 8.3: the exact argument list, and the exact environment."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from townrecord import proc
from townrecord.capture.command import (
    DEFAULT_CLIENT,
    PLAYER_CLIENTS,
    bot_check_marker,
    capture_argv,
    rate_limit_marker,
    run_capture,
    with_player_client,
)
from townrecord.runtime.javascript import FALLBACK_RUNTIME

FOLDER = Path("/storage/work/abc123XYZ")
ARCHIVE = Path("/storage/work/download-archive.txt")
URL = "https://www.youtube.com/watch?v=abc123XYZ"
INTERPRETER = "python"

#: Spec 8.3, written out in the order the spec writes it. Nothing here is
#: derived from the code: this is the text of the spec as a list.
SPEC_8_3 = [
    "python",
    "-m",
    "yt_dlp",
    "--skip-download",
    "--write-subs",
    "--write-auto-subs",
    "--write-info-json",
    "--sub-langs",
    "en",
    "--sub-format",
    "srv3/vtt/best",
    "--js-runtimes",
    "node",
    "--sleep-subtitles",
    "2",
    "--sleep-requests",
    "1",
    "--download-archive",
    "/storage/work/download-archive.txt",
    "--paths",
    "/storage/work/abc123XYZ",
    "-o",
    "/storage/work/abc123XYZ/%(id)s.%(ext)s",
    "https://www.youtube.com/watch?v=abc123XYZ",
]


def test_the_command_is_the_spec_8_3_command() -> None:
    """The whole argument list, compared element by element."""
    assert (
        capture_argv(
            interpreter=INTERPRETER, work_dir=FOLDER, archive=ARCHIVE, url=URL, resume=False
        )
        == SPEC_8_3
    )


def test_a_stopped_capture_resumes_with_continue_in_the_same_folder() -> None:
    """Spec 8.3: --continue, in the folder the earlier run used."""
    argv = capture_argv(
        interpreter=INTERPRETER, work_dir=FOLDER, archive=ARCHIVE, url=URL, resume=True
    )
    assert argv.index("--continue") == argv.index("--paths") + 2
    assert argv == SPEC_8_3[:21] + ["--continue"] + SPEC_8_3[21:]


@pytest.mark.skipif(sys.platform != "win32", reason="a backslash separates only on Windows")
def test_a_windows_folder_is_written_with_forward_slashes() -> None:
    """yt-dlp reads the folder itself, and a backslash is an escape there."""
    argv = capture_argv(
        interpreter=INTERPRETER,
        work_dir=r"C:\storage\work\abc123XYZ",
        archive=r"C:\storage\work\download-archive.txt",
        url=URL,
        resume=False,
    )
    assert "C:/storage/work/abc123XYZ" in argv
    assert "C:\\storage\\work\\abc123XYZ" not in argv


def test_a_posix_folder_passes_through_unchanged() -> None:
    """The twin of the Windows case above, and it runs on every system.

    A POSIX path has no backslash in it, so there is nothing to convert: the
    folder reaches yt-dlp exactly as the caller wrote it. This is the half of
    the rule that macOS and Linux CI checks.
    """
    argv = capture_argv(
        interpreter=INTERPRETER,
        work_dir="/storage/work/abc123XYZ",
        archive="/storage/work/download-archive.txt",
        url=URL,
        resume=False,
    )
    assert "/storage/work/abc123XYZ" in argv
    assert argv[argv.index("--paths") + 1] == "/storage/work/abc123XYZ"
    assert argv[argv.index("-o") + 1] == "/storage/work/abc123XYZ/%(id)s.%(ext)s"


@pytest.mark.skipif(sys.platform == "win32", reason="a backslash separates on Windows")
def test_a_backslash_is_an_ordinary_character_on_posix() -> None:
    """On POSIX the Windows spelling is one file name, and it is left alone.

    Nothing is rewritten, because a backslash is not a separator there. The
    product is right on both systems; only the expectation differs. Written
    after the Ubuntu and macOS CI run of PR #10 failed on exactly this.
    """
    argv = capture_argv(
        interpreter=INTERPRETER,
        work_dir=r"C:\storage\work\abc123XYZ",
        archive=r"C:\storage\work\download-archive.txt",
        url=URL,
        resume=False,
    )
    assert argv[argv.index("--paths") + 1] == "C:\\storage\\work\\abc123XYZ"


def test_the_environment_carries_no_secret(monkeypatch) -> None:
    """PROJECT-BRIEF rule E: the child gets the allow-list, not this process."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-for-yt-dlp")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-not-for-yt-dlp")
    monkeypatch.setenv("PYTHONPATH", "C:/somewhere/else")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "not-for-yt-dlp")

    seen: dict[str, str] = {}

    def runner(argv, *, timeout_s, env):
        seen.update(env)
        return proc.ProcessResult(argv=tuple(argv), returncode=0, stdout=b"", stderr="")

    run_capture(runner, SPEC_8_3, timeout_s=30.0)

    assert "OPENAI_API_KEY" not in seen
    assert "GITHUB_TOKEN" not in seen
    assert "PYTHONPATH" not in seen
    assert "AWS_SECRET_ACCESS_KEY" not in seen
    assert set(seen) <= set(proc.ALLOWED_ENV_NAMES)
    assert "sk-not-for-yt-dlp" not in " ".join(seen.values())


def test_a_rate_limit_is_recognized_in_the_words_yt_dlp_uses() -> None:
    assert rate_limit_marker("ERROR: HTTP Error 429: Too Many Requests") == "http error 429"
    assert rate_limit_marker("error: too many requests") == "too many requests"
    assert rate_limit_marker("HTTP 429") == "http 429"
    assert rate_limit_marker("ERROR: unable to download video data") is None
    assert rate_limit_marker("") is None
    assert rate_limit_marker(None) is None


def test_a_bot_check_is_recognized_in_the_words_youtube_uses() -> None:
    """The sentence YouTube answers a signed-out reader with, tested 2026-09-27."""
    assert (
        bot_check_marker("ERROR: [youtube] abc123XYZ: Sign in to confirm you're not a bot.")
        == "sign in to confirm you're not a bot"
    )
    assert (
        bot_check_marker("Sign in to confirm you are not a bot.")
        == "sign in to confirm you are not a bot"
    )
    assert bot_check_marker("ERROR: HTTP Error 429: Too Many Requests") is None
    assert bot_check_marker("") is None
    assert bot_check_marker(None) is None


def test_a_bot_check_and_a_rate_limit_are_never_read_as_each_other() -> None:
    """One waits for YouTube to forget us, the other retries with another client."""
    printed = (
        "WARNING: Only images are available for download. "
        "No supported JavaScript runtime could be found\n"
        "ERROR: [youtube] abc123XYZ: Sign in to confirm you're not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication.\n"
    )
    assert bot_check_marker(printed) is not None
    assert rate_limit_marker(printed) is None


def test_the_clients_are_the_three_that_answered_with_no_login() -> None:
    """Tested 2026-09-27: these three answered; web_embedded, ios and mweb refused."""
    assert PLAYER_CLIENTS == ("android_vr", "visionos", "tv_embedded")
    assert DEFAULT_CLIENT == "default"


def test_the_retry_names_one_player_client_and_changes_nothing_else() -> None:
    """Spec 8.3: the retry is the command that failed with one argument added.

    The one difference sits right after ``--js-runtimes``, so a reader comparing
    the two argument lists sees it immediately.
    """
    option = ["--extractor-args", "youtube:player_client=android_vr"]
    argv = with_player_client(SPEC_8_3, "android_vr")
    assert argv == SPEC_8_3[:13] + option + SPEC_8_3[13:]
    # And the list it was handed is left alone.
    assert SPEC_8_3[13] == "--sleep-subtitles"


def test_a_command_with_no_js_runtime_option_still_gets_a_client() -> None:
    """Nothing builds such a command, and a caller may hand anything over."""
    argv = with_player_client(["python", "-m", "yt_dlp", URL], "visionos")
    assert argv == [
        "python",
        "-m",
        "yt_dlp",
        "--extractor-args",
        "youtube:player_client=visionos",
        URL,
    ]


def test_the_javascript_runtime_is_whatever_the_caller_names() -> None:
    """Spec 8.3, 8.9: the value comes from the private runtime, not from here."""
    argv = capture_argv(
        interpreter=INTERPRETER,
        work_dir=FOLDER,
        archive=ARCHIVE,
        url=URL,
        js_runtime="deno:/opt/venv/bin/deno",
    )
    assert argv == SPEC_8_3[:12] + ["deno:/opt/venv/bin/deno"] + SPEC_8_3[13:]


def test_a_caller_who_names_no_runtime_gets_the_bare_fallback() -> None:
    """A machine with no private runtime yet still gets a command built."""
    argv = capture_argv(interpreter=INTERPRETER, work_dir=FOLDER, archive=ARCHIVE, url=URL)
    assert argv[argv.index("--js-runtimes") + 1] == FALLBACK_RUNTIME


def test_a_retry_keeps_the_runtime_it_was_given() -> None:
    """The client is added; the JavaScript runtime of the first run is kept."""
    argv = capture_argv(
        interpreter=INTERPRETER,
        work_dir=FOLDER,
        archive=ARCHIVE,
        url=URL,
        js_runtime="deno:/opt/venv/bin/deno",
        player_client="tv_embedded",
    )
    assert argv[argv.index("--js-runtimes") + 1] == "deno:/opt/venv/bin/deno"
    assert argv[argv.index("--extractor-args") - 1] == "deno:/opt/venv/bin/deno"
    assert argv[argv.index("--extractor-args") + 1] == "youtube:player_client=tv_embedded"
