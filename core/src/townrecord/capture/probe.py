"""The known-video test of spec 8.9.

Spec 8.9 switches to a new yt-dlp only when the new version has been tested
against one known video. This module is that test: it runs the caption command
of spec 8.3 against the video the user named, into a scratch folder of its own,
and answers whether the run produced captions.

What it does not do is decide what a failure means. The manager of spec 8.9
takes a ``Probe`` -- ``Callable[[str], bool]``, handed the interpreter of the
candidate runtime -- and it records the reason itself, so this module never
writes a state and never pauses a job.

The folder is a temporary one and is thrown away: the test's captions are not
the user's record of anything, and the video it fetches is the one video that
was chosen for exactly this. The command is built by
:func:`townrecord.capture.command.capture_argv` and run by
:func:`townrecord.capture.command.run_capture`, so the test runs the same
command a capture runs, with the same allow-listed environment and no shell.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .. import proc
from . import command, work

#: How long one known-video test may take. It is longer than a capture's own
#: default because it is one video and one run, and it is not repeated.
DEFAULT_PROBE_TIMEOUT_S = 300.0

#: The download archive inside the scratch folder. It holds this one run and is
#: thrown away with the folder, so the test is never skipped as already seen.
PROBE_ARCHIVE_NAME = "probe.archive"

#: The prefix of the scratch folder, so a folder a killed run left behind is
#: recognizable as this test's rather than the user's work.
PROBE_FOLDER_PREFIX = "townrecord-probe-"

#: What the test says when there is no video to test against (spec 8.9 names
#: no video: the user chooses the one known to them).
NO_TEST_VIDEO = (
    "No test video is set, so a new version cannot be tested before it is used. "
    "Set TOWNRECORD_TEST_VIDEO to the one known video the update is tested against."
)


class NoTestVideo(RuntimeError):
    """The test was asked to run with no video to run against."""


def capture_probe(
    *,
    test_video_url: str,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
    runner: command.Runner | None = None,
    js_runtime: str | None = None,
):
    """Return the known-video test: an interpreter in, a pass or fail out.

    The returned callable takes the interpreter of the runtime being tested, so
    the version the manager is considering is the version the test ran, and
    never the one already active.

    A run that produced no caption file is a failed test even when the command
    exited zero: yt-dlp can finish without the subtitles it was asked for, and
    a version that stops writing captions is exactly what this test is for.
    """
    url = str(test_video_url or "").strip()

    def probe(interpreter: str) -> bool:
        if not url:
            raise NoTestVideo(NO_TEST_VIDEO)
        with tempfile.TemporaryDirectory(
            prefix=PROBE_FOLDER_PREFIX, ignore_cleanup_errors=True
        ) as folder:
            scratch = Path(folder)
            argv = command.capture_argv(
                interpreter=interpreter,
                work_dir=scratch,
                archive=scratch / PROBE_ARCHIVE_NAME,
                url=url,
                js_runtime=js_runtime,
            )
            result = command.run_capture(
                proc.run if runner is None else runner, argv, timeout_s=timeout_s
            )
            if not result.ok:
                return False
            return work.caption_file(scratch) is not None

    return probe


__all__ = [
    "DEFAULT_PROBE_TIMEOUT_S",
    "NO_TEST_VIDEO",
    "PROBE_ARCHIVE_NAME",
    "PROBE_FOLDER_PREFIX",
    "NoTestVideo",
    "capture_probe",
]
