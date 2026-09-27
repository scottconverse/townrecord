"""Running a child process with an allow-listed environment, never a shell.

Spec 8.3 (last rule) and PROJECT-BRIEF rule E: an external tool such as
yt-dlp runs with the environment this process chooses, not with the whole
environment. The parent's environment carries API keys, tokens and paths
that a media downloader has no business reading, so every child gets a
short allow-list instead.

Three habits live here, once, so later units do not each invent them:

* the command is an argument list, never one string handed to a shell: this
  file asks for no shell anywhere, so quoting cannot become a bug;
* the environment is built from :data:`ALLOWED_ENV_NAMES` only;
* a timeout kills the child and is reported as :class:`ProcessTimedOut`,
  which is a failure with a reason, never a quiet empty answer.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The only environment variable names a child process may inherit.
#:
#: Names are compared without case: Windows spells them ``Path`` and ``TEMP``,
#: POSIX spells them ``PATH`` and ``TMPDIR``. The keys of the environment this
#: module builds always use the spelling below.
#:
#: What is deliberately absent: anything holding a secret (``*_API_KEY``,
#: ``*_TOKEN``, ``AWS_*``), and anything that changes what the child's Python
#: imports (``PYTHONPATH``, ``PYTHONHOME``, ``PYTHONSTARTUP``).
ALLOWED_ENV_NAMES: tuple[str, ...] = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)


def allowed_environment(parent: Mapping[str, str] | None = None) -> dict[str, str]:
    """The child's environment: the allow-list, and nothing else.

    ``parent`` defaults to :data:`os.environ`. The answer holds only names
    from :data:`ALLOWED_ENV_NAMES` that the parent actually has, in that
    order, with empty values dropped.
    """
    source = os.environ if parent is None else parent
    allowed = {name.upper(): name for name in ALLOWED_ENV_NAMES}
    found: dict[str, str] = {}
    for name, value in source.items():
        wanted = allowed.get(name.upper())
        if wanted is not None and value:
            found[wanted] = value
    return {name: found[name] for name in ALLOWED_ENV_NAMES if name in found}


class ProcessFailed(RuntimeError):
    """The child could not be started at all (no such program, no permission)."""


class ProcessTimedOut(RuntimeError):
    """The child ran past its limit and was killed.

    A timeout is a failure with a reason attached, so a caller never reads
    an empty answer as "this channel posts nothing".
    """

    def __init__(self, argv: Sequence[str], timeout_s: float) -> None:
        self.argv = tuple(argv)
        self.timeout_s = timeout_s
        program = self.argv[0] if self.argv else "the child process"
        super().__init__(f"{program} timed out after {timeout_s:g} seconds and was stopped")


@dataclass(frozen=True)
class ProcessResult:
    """What a finished child left behind."""

    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def last_stderr_line(self) -> str:
        """The last non-empty line of stderr: usually the reason a tool gives."""
        lines = [line.strip() for line in self.stderr.splitlines() if line.strip()]
        return lines[-1] if lines else ""


def run(
    argv: Sequence[str],
    *,
    timeout_s: float,
    env: Mapping[str, str],
    cwd: str | None = None,
) -> ProcessResult:
    """Run ``argv`` with ``env``, capture its output, and never use a shell.

    There is no default for ``env`` on purpose: a caller has to say which
    environment the child gets, and the usual answer is
    :func:`allowed_environment`.
    """
    if isinstance(argv, (str, bytes)):
        raise TypeError("argv must be an argument list, not one string: a shell is never used")
    command = [str(part) for part in argv]
    if not command:
        raise TypeError("argv must hold at least the program to run")

    options: dict[str, Any] = {
        "capture_output": True,
        "shell": False,
        "env": dict(env),
        "timeout": timeout_s,
        "cwd": cwd,
        "check": False,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        # Keep a console window from flashing on the user's desktop.
        options["creationflags"] = subprocess.CREATE_NO_WINDOW

    try:
        finished = subprocess.run(command, **options)
    except subprocess.TimeoutExpired as exc:
        raise ProcessTimedOut(command, timeout_s) from exc
    except OSError as exc:
        raise ProcessFailed(f"{command[0]} could not be started: {exc}") from exc

    return ProcessResult(
        argv=tuple(command),
        returncode=finished.returncode,
        stdout=finished.stdout or b"",
        stderr=(finished.stderr or b"").decode("utf-8", errors="replace"),
    )


def run_allowlisted(
    argv: Sequence[str], *, timeout_s: float, cwd: str | None = None
) -> ProcessResult:
    """Run ``argv`` with :func:`allowed_environment` and no shell."""
    return run(argv, timeout_s=timeout_s, env=allowed_environment(), cwd=cwd)
