"""The private tool runtime: install, test, switch, roll back (spec 8.9, 14.4).

TownRecord owns its tools. Each one lives in its own venv under an app-data
root the user chose, one folder per version::

    <app-data>/runtimes/yt-dlp/current.json          the pointer
    <app-data>/runtimes/yt-dlp/update.lock           one update at a time
    <app-data>/runtimes/yt-dlp/2026.8.19/Scripts/python.exe
    <app-data>/runtimes/textflowkit/current.json     the same, for spec 8.5

which tool is which is :mod:`townrecord.runtime.tools`, so this class is the
same code for both. The capture job asks :meth:`RuntimeManager.interpreter` for
the Python that runs ``-m yt_dlp``, so the child never depends on what the
system Python happens to have installed. When there is no usable runtime, that
call raises with a plain sentence instead of falling back to the system Python:
the fallback is exactly the failure this unit answers (a user-site yt-dlp the
allow-listed child cannot see).

The transcription job needs a program rather than a module, because spec 8.5
runs TextFlowKit's own console script. That is
:meth:`RuntimeManager.program`, and it is the same pointer, the same pin and
the same rollback as the interpreter.

The daily update of spec 8.9 is :meth:`check_and_update`: ask PyPI, install a
newer version beside the current one, run the known-video test against the new
venv, and move the pointer only when that test passes. A failed test leaves the
active version exactly where it was and returns a plain reason, and the version
that was active stays installed, so :meth:`rollback` can swap back.

Every child here is an argument list with the allow-listed environment and no
shell (spec 8.3, PROJECT-BRIEF rule E). ``uv`` is found on PATH or taken as a
setting.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .. import proc, storage
from . import javascript as javascript_runtime
from . import pypi, tools, versions
from .lock import UpdateLock
from .pointer import Installed, Pointer, read_pointer, write_pointer
from .settings import RUNTIMES_FOLDER, TOOL_NAME, RuntimeSettings

logger = logging.getLogger(__name__)

#: How a child process is run. Tests pass a fake that answers like `uv`.
Runner = Callable[..., proc.ProcessResult]

#: The known-video test of spec 8.9: it gets an interpreter path and answers
#: whether that interpreter captured the test video.
Probe = Callable[[str], bool]

#: A version becomes a folder name, so it is a plain word: letters, digits,
#: dots, dashes and underscores, never a separator and never a leading dash.
SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RuntimeToolMissing(RuntimeError):
    """`uv` is not on this machine, so a runtime cannot be built."""


class RuntimeInstallFailed(RuntimeError):
    """The runtime was not installed, with the reason uv or the venv gave."""


class RuntimeNotInstalled(RuntimeError):
    """There is no usable runtime. The user is told to run setup."""


class RollbackUnavailable(RuntimeError):
    """There is no earlier version to go back to."""


@dataclass(frozen=True)
class InstallResult:
    """What one install left behind."""

    #: The version that was asked for, which becomes the folder name.
    requested: str
    #: The version the venv reported when it was asked. This is the trusted
    #: one (spec 8.9): the request is a wish, the venv is the fact.
    version: str
    #: The venv folder, from the app-data root, with forward slashes.
    venv: str
    #: The interpreter in that venv, as a full path.
    python: str
    #: The value of ``--js-runtimes`` for that venv (spec 8.3, 8.9): the
    #: JavaScript runtime installed beside the interpreter, by full path.
    javascript: str = ""

    @property
    def matches_request(self) -> bool:
        """Return True when the venv reported the version that was asked for."""
        return versions.same_version(self.requested, self.version)


@dataclass(frozen=True)
class UpdateOutcome:
    """What one daily check did, in words the user can read (spec 16.3)."""

    #: What PyPI said the latest version is.
    checked: str
    #: The version that was asked for, or None when nothing was installed.
    requested: str | None
    #: The version that is active after the check.
    active: str | None
    #: The version that was active before the check.
    previous: str | None
    switched: bool
    #: True when the update could not be applied. The active version is
    #: untouched in that case.
    failed: bool
    reason: str


def _safe_version(version: str) -> str:
    """Return a version that is safe to use as a folder name."""
    text = str(version).strip()
    if not SAFE_VERSION.match(text):
        raise RuntimeInstallFailed(f"{version!r} is not a version TownRecord will install.")
    return text


def _tail(result: proc.ProcessResult) -> str:
    """The plainest thing to say about a failed child: its last line."""
    last = result.last_stderr_line()
    return last or f"it exited with {result.returncode} and said nothing."


@dataclass
class RuntimeManager:
    """The private runtime folder for one tool, under the user's app-data root."""

    #: The app-data root the user chose. Required, and never a default: the
    #: runtime must not end up inside the application folder.
    root: Path
    #: Which tool this manager installs: ``yt-dlp`` or ``textflowkit``
    #: (spec 8.9, 14.4). The name is the folder, the pointer and the pin.
    tool: str = TOOL_NAME
    settings: RuntimeSettings = field(default_factory=RuntimeSettings)
    #: The path of `uv`. None means look for it on PATH.
    uv_path: str | None = None
    #: The Python `uv venv` should build from. None leaves the choice to uv.
    python: str | None = None
    #: How a child is run. None means the real one, :func:`townrecord.proc.run`.
    runner: Runner | None = None

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if not self.root.is_absolute():
            raise ValueError("The app-data root must be an absolute path.")
        application = storage.application_folder()
        if storage.is_inside(self.root.resolve(), application):
            raise ValueError("The app-data root must be outside the application folder.")
        # Raises UnknownTool for a name TownRecord does not manage, so a typo
        # never becomes a folder of its own under the user's app-data root.
        self.spec = tools.tool_spec(self.tool)
        if self.runner is None:
            self.runner = proc.run

    # -- where things live -------------------------------------------------

    @property
    def tool_root(self) -> Path:
        """The folder that holds every version of the tool, and the pointer."""
        return self.root / RUNTIMES_FOLDER / self.tool

    def venv_folder(self, version: str) -> Path:
        """The venv folder of one version."""
        return self.tool_root / _safe_version(version)

    def python_in(self, venv: str | Path) -> Path:
        """The interpreter inside a venv folder, on this operating system."""
        return tools.python_in(venv)

    def program_in(self, venv: str | Path) -> Path:
        """The tool's own program inside a venv folder, on this system."""
        return tools.program_in(venv, self.spec.program)

    def uv_executable(self) -> str:
        """Return the path of `uv`, from the setting or from PATH."""
        found = (self.uv_path or "").strip() or shutil.which("uv")
        if not found:
            raise RuntimeToolMissing(
                f"uv was not found, so TownRecord cannot install {self.tool}. "
                "Install uv, or set its path in settings."
            )
        return found

    def _venv_of_active(self, installed: Installed) -> Path | None:
        """The resolved venv of an installed runtime, or None when it is elsewhere."""
        # Resolve before checking: the check is lexical, so a folder written as
        # `runtimes/../..` would otherwise pass it and then run from outside
        # the root the user chose.
        venv = (self.root / installed.venv).resolve()
        if not storage.is_inside(venv, self.root.resolve()):
            logger.warning("The pointer names a runtime outside the app-data root: %s", venv)
            return None
        return venv

    def _installed_python(self, installed: Installed | None) -> Path | None:
        """The interpreter of an installed runtime, or None when it is not there."""
        if installed is None:
            return None
        venv = self._venv_of_active(installed)
        if venv is None:
            return None
        python = self.python_in(venv)
        return python if python.is_file() else None

    def _active(self) -> Installed:
        """The active runtime from the pointer, or raise saying to run setup."""
        try:
            installed = self.read_pointer().active
        except Exception as exc:  # a pointer that cannot be read is not a runtime
            raise RuntimeNotInstalled(
                f"The {self.tool} pointer could not be read ({exc}). "
                f"Run setup to install {self.tool}."
            ) from exc
        if installed is None:
            raise RuntimeNotInstalled(f"{self.tool} is not installed yet; run setup.")
        return installed

    def _not_where_the_pointer_says(self, installed: Installed) -> RuntimeNotInstalled:
        """The plain sentence for a pointer that names a runtime that is not there."""
        return RuntimeNotInstalled(
            f"The {self.tool} runtime {installed.version} is not where the pointer says it is "
            f"({installed.venv}); run setup to install it again."
        )

    # -- running a child ---------------------------------------------------

    def _run(self, argv: Sequence[str], timeout_s: float) -> proc.ProcessResult:
        """Run one child with the allow-listed environment and no shell."""
        return self.runner(argv, timeout_s=timeout_s, env=proc.allowed_environment())

    def read_pointer(self) -> Pointer:
        """Return the pointer file's contents."""
        return read_pointer(self.tool_root)

    # -- what the jobs ask ------------------------------------------------

    def interpreter(self) -> str:
        """Return the interpreter of the active runtime.

        A missing or broken runtime is a plain error and never a fallback to
        the system Python (spec 8.9). The system Python may well be able to
        import yt-dlp, and that is exactly the state TownRecord must not
        depend on: the allow-listed child environment cannot see a user-site
        install.
        """
        installed = self._active()
        python = self._installed_python(installed)
        if python is None:
            raise self._not_where_the_pointer_says(installed)
        return str(python)

    def javascript(self) -> str:
        """Return the value of ``--js-runtimes`` for the active runtime.

        YouTube's pages need a JavaScript runtime, and a user's computer may
        have no Node (spec 8.3, 8.9). So the runtime is the one installed
        beside this tool, in the folder of the venv this manager built: the
        interpreter of the active runtime has its runtime next to it, and the
        value names that program by its full path. When the program is not
        there, the bare fallback name is returned, which is a name yt-dlp
        searches for itself rather than a path that may not exist.
        """
        return javascript_runtime.resolve(self.interpreter()).argument

    def program(self) -> str:
        """Return the tool's own program in the active runtime.

        Spec 8.5 runs TextFlowKit as a program rather than as ``python -m``, so
        the transcription job asks for this path the same way the capture job
        asks for :meth:`interpreter`. The check is the same one: the program
        must be inside the app-data root the user chose, and a pointer that
        names anything else is a plain error rather than a program from
        somewhere TownRecord did not install.
        """
        installed = self._active()
        venv = self._venv_of_active(installed)
        if venv is None:
            raise self._not_where_the_pointer_says(installed)
        program = self.program_in(venv)
        if not program.is_file():
            raise RuntimeNotInstalled(
                f"The {self.tool} runtime {installed.version} holds no {self.spec.program} "
                f"program ({installed.venv}); run setup to install it again."
            )
        return str(program)

    # -- installing --------------------------------------------------------

    def install(self, version: str) -> InstallResult:
        """Build a venv for `version` and install the tool into it.

        The version recorded in the answer is the one the new venv reports when
        asked, not the one this call asked for (spec 8.9).
        """
        requested = _safe_version(version)
        uv = self.uv_executable()
        target = self.venv_folder(requested)
        self._discard_venv(target)
        argv = [uv, "venv", str(target)]
        if self.python:
            argv += ["--python", str(self.python)]
        created = self._run(argv, self.settings.install_timeout_s)
        if not created.ok:
            raise RuntimeInstallFailed(
                f"uv could not create the runtime folder for {self.tool} {requested}: "
                f"{_tail(created)}"
            )
        python = self.python_in(target)
        if not python.is_file():
            raise RuntimeInstallFailed(
                f"uv reported success but {target} holds no Python interpreter."
            )
        installed = self._run(
            [uv, "pip", "install", "--python", str(python), f"{self.tool}=={requested}"],
            self.settings.install_timeout_s,
        )
        if not installed.ok:
            raise RuntimeInstallFailed(
                f"uv could not install {self.tool} {requested}: {_tail(installed)}"
            )
        for companion in self.spec.companions:
            self._install_companion(companion, uv, python, target, requested)
        reported = self._ask_version(python, target)
        if not versions.same_version(requested, reported):
            logger.warning(
                "Asked for %s %s and the venv reports %s; the venv is the fact.",
                self.tool,
                requested,
                reported,
            )
        result = InstallResult(
            requested=requested,
            version=reported,
            venv=target.relative_to(self.root).as_posix(),
            python=str(python),
            javascript=javascript_runtime.resolve(str(python)).argument,
        )
        logger.info("Installed %s %s in %s.", self.tool, result.version, result.venv)
        return result

    def _install_companion(
        self, companion: tools.Companion, uv: str, python: Path, target: Path, requested: str
    ) -> None:
        """Install one companion package into the venv and probe its program.

        A venv that holds yt-dlp but no JavaScript runtime is not the runtime
        of spec 8.9: on a machine with no Node the captures would fail with
        "No supported JavaScript runtime could be found" and YouTube would
        answer a bot check. So a companion that does not answer with its own
        program is a failed install and not a runtime, and the sentence says
        which program is missing.
        """
        installed = self._run(
            [uv, "pip", "install", "--python", str(python), companion.pin],
            self.settings.install_timeout_s,
        )
        if not installed.ok:
            raise RuntimeInstallFailed(
                f"uv could not install {companion.package} into the {self.tool} runtime "
                f"{requested}: {_tail(installed)}"
            )
        program = tools.program_in(target, companion.program)
        if not program.is_file():
            raise RuntimeInstallFailed(
                f"uv reported success but the {self.tool} runtime {requested} holds no "
                f"{companion.program} program, so a capture would find no JavaScript runtime. "
                f"Node is not needed: {companion.package} is installed beside {self.tool}."
            )

    def _ask_version(self, python: Path, venv: Path) -> str:
        """Ask the venv which version it holds. This is the version recorded."""
        result = self._run(
            self.spec.version_argv(python=python, venv=venv), self.settings.version_timeout_s
        )
        if not result.ok:
            raise RuntimeInstallFailed(
                f"The new {self.tool} runtime would not say which version it holds: {_tail(result)}"
            )
        text = result.stdout.decode("utf-8", errors="replace")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            raise RuntimeInstallFailed(
                f"The new {self.tool} runtime answered no version, so nothing can be pinned."
            )
        return lines[0]

    def _discard_venv(self, target: Path) -> None:
        """Remove a venv folder for a version about to be built again."""
        if target == self.tool_root or not storage.is_inside(target, self.tool_root):
            return
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    # -- updating and rolling back ----------------------------------------

    def check_and_update(
        self, test_video_url: str, probe: Probe, *, client: httpx.Client
    ) -> UpdateOutcome:
        """Check PyPI, install a newer version, test it, switch if it passes.

        The known-video test is the only thing that decides (spec 8.9): a
        version that fails it is never made active, and the reason is returned
        as a plain sentence. Only one update runs at a time, enforced by the
        lock file of :mod:`townrecord.runtime.lock`.
        """
        latest = pypi.latest_version(client, tool=self.tool)
        with UpdateLock(self.tool_root, stale_after_s=self.settings.lock_stale_s, tool=self.tool):
            pointer = self.read_pointer()
            current = pointer.active if self._installed_python(pointer.active) else None
            if current is not None and not versions.is_newer(latest, current.version):
                return UpdateOutcome(
                    checked=latest,
                    requested=None,
                    active=current.version,
                    previous=None if pointer.previous is None else pointer.previous.version,
                    switched=False,
                    failed=False,
                    reason=f"{self.tool} {current.version} is already the newest version.",
                )
            result = self.install(latest)
            passed, why = self._probe(result.python, probe)
            kept = current.version if current is not None else None
            if not passed:
                return UpdateOutcome(
                    checked=latest,
                    requested=result.requested,
                    active=kept,
                    previous=None if pointer.previous is None else pointer.previous.version,
                    switched=False,
                    failed=True,
                    reason=(
                        f"The new {self.tool} {result.version} failed its test against "
                        f"{test_video_url} ({why}), so {self._kept_phrase(kept)} stayed in place "
                        "and the new version was not made active."
                    ),
                )
            write_pointer(
                self.tool_root,
                Pointer(
                    active=Installed(version=result.version, venv=result.venv),
                    previous=current,
                ),
            )
            return UpdateOutcome(
                checked=latest,
                requested=result.requested,
                active=result.version,
                previous=kept,
                switched=True,
                failed=False,
                reason=(
                    f"{self.tool} {result.version} passed its test against {test_video_url} "
                    "and is now the active version."
                ),
            )

    def _kept_phrase(self, kept: str | None) -> str:
        return f"{self.tool} {kept}" if kept else "no version (there was none that worked)"

    def _probe(self, python: str, probe: Probe) -> tuple[bool, str]:
        """Run the known-video test. A test that blows up is a failed test."""
        try:
            passed = bool(probe(python))
        except Exception as exc:
            text = " ".join(str(exc).split()) or "no reason given"
            return False, f"the test raised {type(exc).__name__}: {text}"
        if passed:
            return True, "the test passed"
        return False, "the test reported a failure"

    def rollback(self) -> Installed:
        """Make the previous version active again, and return it.

        A rollback is an explicit user action, so it takes the same lock an
        update does: swapping the pointer while an update is midway through
        would leave the two disagreeing about which version is active.
        """
        with UpdateLock(self.tool_root, stale_after_s=self.settings.lock_stale_s, tool=self.tool):
            pointer = self.read_pointer()
            earlier = pointer.previous
            if earlier is None:
                raise RollbackUnavailable(f"There is no earlier {self.tool} to go back to.")
            if self._installed_python(earlier) is None:
                raise RollbackUnavailable(
                    f"The earlier {self.tool} {earlier.version} is not on disk any more "
                    f"({earlier.venv}), so there is nothing to go back to."
                )
            write_pointer(self.tool_root, pointer.swapped())
        logger.info("Rolled %s back to %s.", self.tool, earlier.version)
        return earlier
