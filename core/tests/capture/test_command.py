"""The command of spec 8.3: the exact argument list, and the exact environment."""

from __future__ import annotations

from pathlib import Path

from townrecord import proc
from townrecord.capture.command import capture_argv, rate_limit_marker, run_capture

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


def test_the_folder_is_written_with_forward_slashes() -> None:
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
