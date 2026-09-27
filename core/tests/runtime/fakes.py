"""A fake `uv`, a fake PyPI, and the helpers the runtime tests share.

No test here reaches the network and none of them installs anything real: the
runner handed to the manager answers like `uv`, and the PyPI client is built on
``httpx.MockTransport``. The one test that does install for real is
``test_integration_install.py``, which says so in its name and its marker.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from townrecord import proc
from townrecord.jobs import JobContext, claim, enqueue
from townrecord.runtime import RuntimeManager, RuntimeSettings
from townrecord.runtime.pointer import Installed, Pointer, write_pointer
from townrecord.runtime.settings import PYPI_JSON_URL, RUNTIMES_FOLDER, TOOL_NAME
from townrecord.runtime.tools import program_in, program_name

#: A clock the tests move by hand, so no test sleeps for real seconds.
START = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)

#: What the venv of the pinned release reported when asked, live on 2026-09-27:
#: `python -m yt_dlp --version` said `2026.08.19` while PyPI said `2026.8.19`.
VENV_SPELLING = "2026.08.19"
PYPI_SPELLING = "2026.8.19"

#: The packages whose install leaves a console script in the venv, so the fake
#: uv can write the file a companion probe looks for. A package that is not
#: here is a pin the fake records and writes nothing for.
PACKAGE_PROGRAMS = {"deno": "deno"}


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, start: datetime = START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


def result(
    argv: Sequence[str], returncode: int = 0, stdout: str = "", stderr: str = ""
) -> proc.ProcessResult:
    """One finished child, the way :mod:`townrecord.proc` reports it."""
    return proc.ProcessResult(
        argv=tuple(str(part) for part in argv),
        returncode=returncode,
        stdout=stdout.encode("utf-8"),
        stderr=stderr,
    )


class FakeUV:
    """Stands in for `uv`, and for the python inside the venv `uv` builds.

    A call whose program is `uv` creates or fills a venv folder. Any other
    program is the venv's own python, or the tool's own console script, being
    asked for its version, which is what spec 8.9 records.
    """

    def __init__(
        self,
        reported: str = VENV_SPELLING,
        *,
        venv_rc: int = 0,
        install_rc: int = 0,
        version_rc: int = 0,
        version_stderr: str = "",
        build_python: bool = True,
        program: str | None = None,
        companion: bool = True,
    ) -> None:
        self.reported = reported
        self.venv_rc = venv_rc
        self.install_rc = install_rc
        self.version_rc = version_rc
        self.version_stderr = version_stderr
        self.build_python = build_python
        #: The console script this tool's venv holds, when it has one. None
        #: means the tool is asked through ``python -m <module>`` instead.
        self.program = program
        #: False stands for a `uv pip install` that reports success and leaves
        #: the companion out of the venv, which is the failure the install has
        #: to catch for itself rather than trust uv's exit code.
        self.companion = companion
        self.calls: list[dict[str, Any]] = []
        #: Every `<tool>==<version>` pin uv was asked for.
        self.pins: list[str] = []
        #: Interpreter paths, in order, of the "which version are you" calls.
        self.asked: list[str] = []

    def __call__(
        self, argv: Sequence[str], *, timeout_s: float, env: Mapping[str, str]
    ) -> proc.ProcessResult:
        command = [str(part) for part in argv]
        self.calls.append({"argv": command, "timeout_s": timeout_s, "env": dict(env)})
        if Path(command[0]).name.lower().startswith("uv"):
            return self._uv(command)
        self.asked.append(command[0])
        return result(
            command, self.version_rc, stdout=f"{self.reported}\n", stderr=self.version_stderr
        )

    def _uv(self, command: list[str]) -> proc.ProcessResult:
        subcommand = command[1] if len(command) > 1 else ""
        if subcommand == "venv":
            target = Path(command[2])
            if self.venv_rc == 0 and self.build_python:
                python = venv_python(target)
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_text("#!/fake python\n", encoding="utf-8")
            if self.venv_rc == 0 and self.program:
                script = program_in(target, self.program)
                script.parent.mkdir(parents=True, exist_ok=True)
                script.write_text("#!/fake program\n", encoding="utf-8")
            return result(command, self.venv_rc, stderr="" if self.venv_rc == 0 else "boom")
        if subcommand == "pip":
            pin = command[-1]
            self.pins.append(pin)
            if self.install_rc == 0:
                self._install_program(pin, command)
            return result(
                command, self.install_rc, stderr="" if self.install_rc == 0 else "no such version"
            )
        raise AssertionError(f"the fake uv was asked for something else: {command}")

    def _install_program(self, pin: str, command: list[str]) -> None:
        """Write the console script a companion package installs, when uv would.

        A package that installs no program of its own (yt-dlp is asked through
        ``python -m``) leaves nothing behind, the same way it does for real.
        """
        program = PACKAGE_PROGRAMS.get(pin.split("==")[0].strip())
        if program is None or not self.companion:
            return
        try:
            interpreter = Path(command[command.index("--python") + 1])
        except (ValueError, IndexError):  # pragma: no cover - the manager always names one
            return
        script = interpreter.parent / program_name(program)
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text("#!/fake program\n", encoding="utf-8")

    @property
    def pins_requested(self) -> list[str]:
        return list(self.pins)


class FakeProbe:
    """The known-video test of spec 8.9, faked."""

    def __init__(self, passed: bool = True, raises: BaseException | None = None) -> None:
        self.passed = passed
        self.raises = raises
        self.seen: list[str] = []

    def __call__(self, interpreter: str) -> bool:
        self.seen.append(interpreter)
        if self.raises is not None:
            raise self.raises
        return self.passed


def pypi_client(
    version: str = PYPI_SPELLING,
    *,
    status_code: int = 200,
    payload: Any = None,
    error: Exception | None = None,
    calls: list[str] | None = None,
) -> httpx.Client:
    """A client that answers like PyPI's JSON API, or fails on purpose."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if error is not None:
            raise error
        body = payload if payload is not None else {"info": {"version": version}}
        return httpx.Response(status_code, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def venv_python(venv: Path) -> Path:
    """The interpreter inside a venv folder, as the manager spells it."""
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def make_venv(
    root: Path, folder: str, *, tool: str = TOOL_NAME, program: str | None = None
) -> Path:
    """Create the files of a fake venv under the app-data root."""
    venv = root / RUNTIMES_FOLDER / tool / folder
    python = venv_python(venv)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/fake python\n", encoding="utf-8")
    if program:
        script = program_in(venv, program)
        script.write_text("#!/fake program\n", encoding="utf-8")
    return python


def installed(folder: str, version: str, *, tool: str = TOOL_NAME) -> Installed:
    """The pointer entry of a fake venv folder."""
    return Installed(version=version, venv=f"{RUNTIMES_FOLDER}/{tool}/{folder}")


def write_installed(
    root: Path,
    active: Installed | None,
    previous: Installed | None,
    *,
    tool: str = TOOL_NAME,
) -> None:
    """Write a pointer by hand, as an earlier run of the service would have."""
    write_pointer(root / RUNTIMES_FOLDER / tool, Pointer(active=active, previous=previous))


def manager_for(root: Path, runner: Any = None, **kwargs: Any) -> RuntimeManager:
    """A manager whose uv is the fake, never the one on this machine."""
    return RuntimeManager(
        root=root,
        settings=RuntimeSettings(),
        uv_path="uv",
        runner=runner,
        **kwargs,
    )


def pypi_url(tool: str = TOOL_NAME) -> str:
    """The address the latest version is read from."""
    return PYPI_JSON_URL.format(tool=tool)


def claimed_job(
    conn: sqlite3.Connection, kind: str, clock: FakeClock, *, lane: str = "normal"
) -> JobContext:
    """Enqueue one job, claim it, and return its context."""
    job_id = enqueue(conn, kind, None)
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


def checkpoint_of(conn: sqlite3.Connection, job_id: int) -> dict[str, Any]:
    """The checkpoint a finished job left behind."""
    row = conn.execute("SELECT checkpoint FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row is not None and row["checkpoint"]
    return json.loads(row["checkpoint"])
