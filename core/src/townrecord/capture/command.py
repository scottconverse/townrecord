"""The capture command of spec 8.3, and what its exit codes mean.

The command is built as an argument list and handed to :mod:`townrecord.proc`,
which never uses a shell (spec 8.3, PROJECT-BRIEF rule E). The order of the
arguments is the order the spec writes them in, so the two can be read side by
side.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from .. import proc

#: The job kind this module builds the command for.
JOB_KIND = "capture_captions"

#: The job kind runs in the `normal` lane: it is waiting on the network most of
#: the time, and the `heavy` lane is for the one-at-a-time work of spec 16.1.
LANE = "normal"

#: The watch address, for a video whose row carries none.
WATCH_URL = "https://www.youtube.com/watch?v={platform_video_id}"

#: The caption file extensions, in the order spec 8.3 names them.
SRV3 = "srv3"
VTT = "vtt"

#: What yt-dlp says when YouTube rate limits it. The wording is not fixed by
#: anything, so the check is a case-insensitive search for any of these and the
#: one that matched is kept for the user. The coordinator compares this list
#: against a real 429 the first time one is seen.
RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "http error 429",
    "http 429",
    "429 too many requests",
    "too many requests",
)

#: A callable that runs the command. Tests pass a fake that writes files into
#: the --paths folder instead of talking to YouTube.
Runner = Callable[..., proc.ProcessResult]


class CaptureFailed(RuntimeError):
    """The capture did not produce what the job needs, with the reason why."""


def watch_url(url: str | None, platform_video_id: str) -> str:
    """Return the watch address of a video: the listed one, else the canonical one."""
    text = (url or "").strip()
    return text or WATCH_URL.format(platform_video_id=platform_video_id)


def rate_limit_marker(stderr: str | None) -> str | None:
    """Return the text that says YouTube rate limited us, or None.

    Matching is case-insensitive, and the matched needle is returned so the
    reason the user reads quotes what yt-dlp actually printed.
    """
    text = (stderr or "").lower()
    for marker in RATE_LIMIT_MARKERS:
        if marker in text:
            return marker
    return None


def capture_argv(
    *,
    interpreter: str,
    work_dir: str | Path,
    archive: str | Path,
    url: str,
    resume: bool = False,
) -> list[str]:
    """Build the spec 8.3 command as an argument list.

    ``resume`` adds ``--continue``: "A stopped capture resumes with --continue
    in the same folder." Both paths are written with a forward slash. yt-dlp
    reads that text itself, a Windows backslash is an escape in the ``-o``
    template, and one spelling for both means the argument list does not change
    with the operating system.
    """
    folder = Path(work_dir).as_posix()
    argv = [
        interpreter,
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
        Path(archive).as_posix(),
        "--paths",
        folder,
    ]
    if resume:
        argv.append("--continue")
    argv += ["-o", f"{folder}/%(id)s.%(ext)s", url]
    return argv


def run_capture(runner: Runner, argv: Sequence[str], *, timeout_s: float) -> proc.ProcessResult:
    """Run the command with the allow-listed environment and return its result.

    The environment is built here and handed to the runner as a whole, so a
    test can read exactly what the child would have received. There is no
    secret in it: :func:`townrecord.proc.allowed_environment` keeps only the
    names on the allow-list.
    """
    return runner(argv, timeout_s=timeout_s, env=proc.allowed_environment())
