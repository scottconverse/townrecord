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
from townrecord.jobs import DONE, JobContext, claim, enqueue, finish
from townrecord.runtime.settings import RUNTIMES_FOLDER, TOOL_NAME
from townrecord.runtime.tools import program_in, program_name, python_in

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

    The same fake serves the caption command of spec 8.3 and the audio-only
    command of spec 8.5: setting :attr:`audio` puts it in audio mode, where it
    writes an audio file and the sidecar beside it into the folder the ``-o``
    template names, instead of a caption file.
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
    #: When set, this fake is the audio download of spec 8.5: it writes these
    #: bytes as ``<id>.opus`` rather than writing a caption file.
    audio: bytes | None = None
    #: The sidecar the audio download writes beside the audio, when it writes
    #: one. Its ``duration`` is what sets the transcription timeout.
    audio_info: dict[str, Any] | None = field(default_factory=lambda: {"duration": 7200})
    #: When set, YouTube asks for a bot check unless the command named one of
    #: these player clients. ``()` means every client is refused, so the whole
    #: ladder of spec 8.3 fails; ``("visionos",)`` means android_vr is refused
    #: and visionos answers. None means no bot check at all.
    bot_check_until: tuple[str, ...] | None = None
    #: When set, a call naming one of these clients is rate limited instead of
    #: bot-checked, which is how a test puts a 429 in the middle of the ladder.
    rate_limit_clients: tuple[str, ...] = ()
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self, argv: Sequence[str], *, timeout_s: float, env: Mapping[str, str]
    ) -> proc.ProcessResult:
        command = [str(part) for part in argv]
        self.calls.append({"argv": command, "timeout_s": timeout_s, "env": dict(env)})
        self.skipped_by_archive = self._already_captured(command)
        if self.rate_limit_clients and self._player_client(command) in self.rate_limit_clients:
            return proc.ProcessResult(
                argv=tuple(command), returncode=1, stdout=b"", stderr=RATE_LIMIT_STDERR
            )
        if self._refuses_bot_check(command):
            return proc.ProcessResult(
                argv=tuple(command),
                returncode=1,
                stdout=b"",
                stderr=BOT_CHECK_STDERR,
            )
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

    def _refuses_bot_check(self, command: Sequence[str]) -> bool:
        """True when the client this command named is one YouTube will not answer."""
        if self.bot_check_until is None:
            return False
        return self._player_client(command) not in self.bot_check_until

    @staticmethod
    def _player_client(command: Sequence[str]) -> str:
        """The player client this command named, or "" when it named none."""
        for part in command:
            if part.startswith("youtube:player_client="):
                return part.rpartition("=")[2]
        return ""

    @property
    def player_clients(self) -> list[str]:
        """The player client of every call this fake saw, in order.

        "" is the plain command that named none, which is what YouTube serves a
        signed-out reader by default.
        """
        return [self._player_client(call["argv"]) for call in self.calls]

    def _write_output(self, command: Sequence[str]) -> None:
        """Write the files the real yt-dlp would have written."""
        if self.audio is not None:
            self._write_audio(command)
            return
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

    def _write_audio(self, command: Sequence[str]) -> None:
        """Write the audio file, and its sidecar, where the ``-o`` template says.

        The audio command of spec 8.5 writes beside the ``-o`` template and not
        into a ``--paths`` folder, so the folder is read from the template's own
        parent: the same place the real yt-dlp would put the file.
        """
        folder = Path(command[command.index("-o") + 1]).parent
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{self.platform_video_id}.opus").write_bytes(self.audio or b"")
        if self.audio_info is not None:
            sidecar = folder / f"{self.platform_video_id}.info.json"
            sidecar.write_text(json.dumps(self.audio_info), encoding="utf-8")

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


#: What yt-dlp printed on a machine with no JavaScript runtime when YouTube
#: refused, tested 2026-09-27: yt-dlp's own complaint first, then YouTube's
#: sentence. It holds no rate-limit wording, so the two causes stay apart.
BOT_CHECK_STDERR = (
    "WARNING: Only images are available for download. "
    "No supported JavaScript runtime could be found\n"
    "ERROR: [youtube] abc123XYZ: Sign in to confirm you're not a bot. Use --cookies-from-browser "
    "or --cookies for the authentication.\n"
)


def fake_bot_check(*clients: str, **kwargs: Any) -> FakeYtDlp:
    """A fake that refuses every client except the ones named."""
    return FakeYtDlp(bot_check_until=tuple(clients), **kwargs)


def deno_beside(python: Path) -> Path:
    """The JavaScript program a private runtime puts beside this interpreter."""
    return python.parent / program_name("deno")


def private_python(root: Path, *, folder: str = "2026.8.19", deno: bool = True) -> Path:
    """The interpreter of a folder shaped like the private runtime of spec 8.9.

    Written by the same helpers the manager uses, so the file name of the
    interpreter and of the JavaScript program is the one this system spells
    (PROJECT-BRIEF rule 11b): no test here depends on Windows-only names.
    Returns the interpreter, which is what a job is given.
    """
    venv = root / RUNTIMES_FOLDER / TOOL_NAME / folder
    python = python_in(venv)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/fake python\n", encoding="utf-8")
    if deno:
        program_in(venv, "deno").write_text("#!/fake program\n", encoding="utf-8")
    return python


#: A transcript in the shape TextFlowKit writes (spec 8.5): a list of segments,
#: each with seconds and text, and word times when the model gives them. The
#: metadata block is what the provenance of spec 8.5 records.
TRANSCRIPT_DOCUMENT: dict[str, Any] = {
    "metadata": {"model": "small", "device": "cpu"},
    "segments": [
        {
            "start": 0.0,
            "end": 2.5,
            "text": "The council will come to order.",
            "words": [
                {"start": 0.0, "end": 0.4, "text": "The"},
                {"start": 0.4, "end": 1.0, "text": "council"},
            ],
        },
        {"start": 2.5, "end": 5.25, "text": "Roll call, please.", "speaker": "Mayor"},
    ],
}


def transcript_document(**overrides: Any) -> dict[str, Any]:
    """A copy of the fixture transcript, with parts of it replaced."""
    document = json.loads(json.dumps(TRANSCRIPT_DOCUMENT))
    document.update(overrides)
    return document


@dataclass
class FakeTextFlowKit:
    """A TextFlowKit that writes a transcript instead of running a model.

    Spec 8.10 keeps every meeting on this machine, and a real transcription
    loads a model and runs it over hours of audio, so no test here installs the
    tool or runs it. This fake answers the two calls the job of spec 8.5 makes:
    ``--version``, which the provenance needs, and ``transcribe``, which writes
    a JSON file into the ``--output-dir`` and prints its path, the way the real
    tool does.
    """

    document: dict[str, Any] | None = field(
        default_factory=lambda: json.loads(json.dumps(TRANSCRIPT_DOCUMENT))
    )
    #: What ``--version`` prints, as the real tool prints it: name and version.
    version: str = "textflowkit 0.1.6"
    #: When set, what ``--version`` prints instead. An empty string is a tool
    #: that answered nothing, which is a setup the user has to finish.
    version_stdout: str | None = None
    version_rc: int = 0
    returncode: int = 0
    stderr: str = ""
    write_json: bool = True
    json_name: str = f"{PLATFORM_VIDEO_ID}.json"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self, argv: Sequence[str], *, timeout_s: float, env: Mapping[str, str]
    ) -> proc.ProcessResult:
        command = [str(part) for part in argv]
        self.calls.append({"argv": command, "timeout_s": timeout_s, "env": dict(env)})
        if "--version" in command:
            text = self.version if self.version_stdout is None else self.version_stdout
            return proc.ProcessResult(
                argv=tuple(command),
                returncode=self.version_rc,
                stdout=text.encode("utf-8"),
                stderr=self.stderr if self.version_rc else "",
            )
        return self._transcribe(command)

    def _transcribe(self, command: Sequence[str]) -> proc.ProcessResult:
        """Write the JSON the real tool would have written, and print its path."""
        if self.returncode != 0 or not self.write_json:
            return proc.ProcessResult(
                argv=tuple(command), returncode=self.returncode, stdout=b"", stderr=self.stderr
            )
        out_dir = Path(command[command.index("--output-dir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / self.json_name
        body = self.document if self.document is not None else {}
        path.write_text(json.dumps(body), encoding="utf-8")
        return proc.ProcessResult(
            argv=tuple(command), returncode=0, stdout=f"{path}\n".encode(), stderr=""
        )

    @property
    def version_call(self) -> dict[str, Any] | None:
        """The ``--version`` call this fake saw, if it saw one."""
        for call in self.calls:
            if "--version" in call["argv"]:
                return call
        return None

    @property
    def transcribe_calls(self) -> list[dict[str, Any]]:
        """The transcription calls this fake saw, in order."""
        return [call for call in self.calls if "--version" not in call["argv"]]

    @property
    def argv(self) -> list[str]:
        """The argument list of the one transcription call this fake saw."""
        calls = self.transcribe_calls
        assert calls, "the transcription command was never run"
        return calls[0]["argv"]


def claimed_job(
    conn: sqlite3.Connection,
    video_id: int,
    clock: FakeClock,
    *,
    kind: str = JOB_KIND,
    lane: str = "normal",
) -> JobContext:
    """Enqueue one capture job, claim it, and return its context.

    Claiming takes the oldest queued job of a lane, and a capture that stored a
    transcript leaves an alignment job of spec 16.3 in this same lane (part 1 of
    this unit). So a test that captures a video twice finds that job in front of
    the one it just enqueued: it is closed here, because it is not what the test
    is asking for and a later claim would otherwise never reach its own job.
    """
    job_id = enqueue(conn, kind, video_id)
    taken = claim(conn, lane, "worker-1", clock=clock)
    while taken is not None and taken.job_id != job_id:
        finish(conn, taken.job_id, taken.token, DONE, clock=clock)
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
