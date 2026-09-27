"""A fake yt-dlp, and the small fixtures the capture tests are built on.

The fake writes into the ``--paths`` folder exactly the files the real command
would write, and it records the argument list and the environment it was
handed. No test in this folder ever reaches the network: the job's runner is
always this fake (PROJECT-BRIEF rule 4).

The info.json here is the shape a real Longmont capture has: ``subtitles`` is
empty and the English track is in ``automatic_captions``, which the coordinator
read off a real sidecar.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from townrecord import proc
from townrecord.capture.command import JOB_KIND
from townrecord.jobs import JobContext, claim, enqueue

#: A clock the test moves by hand, so no test sleeps for real minutes.
START = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)

#: The video the fixtures capture.
PLATFORM_VIDEO_ID = "abc123XYZ"

#: The excerpt already in the caption fixtures.
FIXTURE_SRV3 = Path(__file__).resolve().parents[1] / "captions" / "fixtures" / "excerpt.srv3"

#: A sidecar with no publisher track and one automatic English track, which is
#: what the real Longmont sidecar looked like.
INFO_AUTO_CAPTIONS: dict[str, Any] = {
    "id": PLATFORM_VIDEO_ID,
    "title": "City Council Regular Session",
    "duration": 7200,
    "live_status": "was_live",
    "subtitles": {},
    "automatic_captions": {"en": [{"ext": "srv3", "name": "English"}]},
}

#: A sidecar with a publisher track: ``subtitles`` holds the one the city
#: uploaded.
INFO_PUBLISHER_CAPTIONS: dict[str, Any] = {
    **INFO_AUTO_CAPTIONS,
    "subtitles": {"en": [{"ext": "vtt", "name": "English"}]},
    "automatic_captions": {},
}


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, start: datetime = START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


@dataclass
class FakeYtDlp:
    """A yt-dlp that writes files instead of talking to YouTube.

    Every call is recorded whole, argument list and environment, so a test can
    compare both against the spec.
    """

    platform_video_id: str = PLATFORM_VIDEO_ID
    caption: bytes = field(default_factory=lambda: FIXTURE_SRV3.read_bytes())
    caption_ext: str = "srv3"
    info: dict[str, Any] | None = field(default_factory=lambda: dict(INFO_AUTO_CAPTIONS))
    returncode: int = 0
    stderr: str = ""
    write_caption: bool = True
    padding_bytes: int = 0
    #: The real yt-dlp skips a video the archive already records, which is the
    #: whole reason a stale archive file can hide a meeting.
    honour_archive: bool = True
    #: Set on every call: True when the archive file made this fake skip the
    #: video, which is how a stale archive hides a meeting.
    skipped_by_archive: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self, argv: Sequence[str], *, timeout_s: float, env: Mapping[str, str]
    ) -> proc.ProcessResult:
        command = [str(part) for part in argv]
        self.calls.append({"argv": command, "timeout_s": timeout_s, "env": dict(env)})
        self.skipped_by_archive = self._already_captured(command)
        if self.returncode == 0 and not self.skipped_by_archive:
            self._write_output(command)
        return proc.ProcessResult(
            argv=tuple(command),
            returncode=self.returncode,
            stdout=b"",
            stderr=self.stderr,
        )

    def _already_captured(self, command: Sequence[str]) -> bool:
        """True when the archive file says this video was already downloaded."""
        if not self.honour_archive or "--download-archive" not in command:
            return False
        archive = Path(command[command.index("--download-archive") + 1])
        if not archive.is_file():
            return False
        return any(
            line.strip().endswith(f" {self.platform_video_id}")
            for line in archive.read_text(encoding="utf-8").splitlines()
        )

    def _write_output(self, command: Sequence[str]) -> None:
        """Write the files the real yt-dlp would have written."""
        folder = Path(command[command.index("--paths") + 1])
        folder.mkdir(parents=True, exist_ok=True)
        if self.write_caption:
            name = f"{self.platform_video_id}.en.{self.caption_ext}"
            (folder / name).write_bytes(self.caption)
        if self.info is not None:
            sidecar = folder / f"{self.platform_video_id}.info.json"
            sidecar.write_text(json.dumps(self.info), encoding="utf-8")
        if self.padding_bytes:
            extra = folder / f"{self.platform_video_id}.extra"
            extra.write_bytes(b"x" * self.padding_bytes)

    @property
    def argv(self) -> list[str]:
        """The argument list of the one call this fake saw. Fails when it saw none."""
        assert self.calls, "the capture command was never run"
        return self.calls[0]["argv"]

    @property
    def env(self) -> dict[str, str]:
        """The environment of the one call this fake saw."""
        assert self.calls, "the capture command was never run"
        return self.calls[0]["env"]


#: What the second fake returns: yt-dlp's own words for a rate limit. The
#: wording is not fixed by anything, which is why the job matches a list of
#: phrases and quotes the one it found.
RATE_LIMIT_STDERR = (
    "WARNING: [youtube] abc123XYZ: Unable to download webpage: HTTP Error 429: Too Many Requests\n"
)


def fake_429(**kwargs: Any) -> FakeYtDlp:
    """A second fake: exit code 1, a 429 on stderr, and nothing written."""
    return FakeYtDlp(returncode=1, stderr=RATE_LIMIT_STDERR, **kwargs)


def claimed_job(
    conn: sqlite3.Connection,
    video_id: int,
    clock: FakeClock,
    *,
    kind: str = JOB_KIND,
    lane: str = "normal",
) -> JobContext:
    """Enqueue one capture job, claim it, and return its context."""
    job_id = enqueue(conn, kind, video_id)
    taken = claim(conn, lane, "worker-1", clock=clock)
    assert taken is not None
    assert taken.job_id == job_id
    return JobContext(
        conn=conn,
        job_id=taken.job_id,
        kind=taken.kind,
        payload=taken.payload,
        lane=taken.lane,
        claim_token=taken.token,
        clock=clock,
    )
