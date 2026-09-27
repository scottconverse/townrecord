"""One real install, through PyPI, into a temporary root (spec 8.9).

Everything else in this folder fakes `uv`. This file does not: it runs the real
`uv` against the real PyPI index with the allow-listed child environment, and
asks the venv it built which version it holds. That is the path the live
capture run takes, and the reason the runtime exists at all is that this path
must work when the system Python's own site-packages does not.

It needs `uv` and the network, so it is marked `integration` and skips with a
clear reason when either is missing. It installs into `tmp_path`, never into
the repository and never into the system Python.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest

from townrecord import proc
from townrecord.runtime.manager import RuntimeManager
from townrecord.runtime.pointer import Installed, Pointer, write_pointer
from townrecord.runtime.settings import TOOL_NAME, RuntimeSettings
from townrecord.runtime.tools import JAVASCRIPT_COMPANION, program_in

from .fakes import VENV_SPELLING, venv_python

#: The release the live run of 2026-09-27 used, and the one this test pins.
PINNED = "2026.8.19"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("uv") is None,
        reason="uv is not on PATH, so a real runtime cannot be built here",
    ),
]


def test_a_real_install_builds_a_private_venv_that_holds_the_pinned_tool(
    app_root: Path,
) -> None:
    """Install 2026.8.19 for real, then ask the venv it is really there."""
    manager = RuntimeManager(root=app_root, settings=RuntimeSettings(), uv_path="uv")

    result = manager.install(PINNED)

    assert result.requested == PINNED
    assert result.version  # the venv spoke for itself
    assert result.venv == f"runtimes/{TOOL_NAME}/{PINNED}"
    assert Path(result.python).is_file()
    assert Path(result.python) == venv_python(app_root / "runtimes" / TOOL_NAME / PINNED)

    # The recorded version is what the venv said, and it is the pinned release
    # even though the two spellings differ (the venv says 2026.08.19).
    assert result.matches_request is True

    # Record it the way setup does: the pointer names the venv that was tested.
    write_pointer(
        manager.tool_root, Pointer(active=Installed(version=result.version, venv=result.venv))
    )
    assert manager.interpreter() == result.python

    # And the child the capture job would run really works, with the same
    # allow-listed environment and no shell (spec 8.3).
    child = proc.run(
        [result.python, "-m", "yt_dlp", "--version"],
        timeout_s=60.0,
        env=proc.allowed_environment(),
    )
    assert child.ok, f"{child.returncode}: {child.last_stderr_line()}"
    assert child.stdout.decode("utf-8", errors="replace").strip() == VENV_SPELLING


def test_a_real_install_puts_the_javascript_runtime_beside_the_tool(
    app_root: Path,
) -> None:
    """Spec 8.3, 8.9: deno is a PyPI project, so one install gives yt-dlp both.

    The point of the whole unit: a user's computer needs no Node. The program is
    checked where a capture looks for it, beside the interpreter of the venv
    that holds yt-dlp, and it is asked for its version with the allow-listed
    environment a capture uses.
    """
    manager = RuntimeManager(root=app_root, settings=RuntimeSettings(), uv_path="uv")

    result = manager.install(PINNED)

    venv = Path(result.python).parent.parent
    deno = program_in(venv, JAVASCRIPT_COMPANION.program)
    assert deno.is_file(), f"uv installed yt-dlp but left {deno} out of the venv"
    assert result.javascript == f"deno:{deno}"

    write_pointer(
        manager.tool_root, Pointer(active=Installed(version=result.version, venv=result.venv))
    )
    assert manager.javascript() == f"deno:{deno}"

    child = proc.run([str(deno), "--version"], timeout_s=60.0, env=proc.allowed_environment())
    assert child.ok, f"{child.returncode}: {child.last_stderr_line()}"
    assert JAVASCRIPT_COMPANION.version in child.stdout.decode("utf-8", errors="replace")


def test_a_real_install_can_reach_pypi_with_the_client_the_job_uses(
    app_root: Path,
) -> None:
    """The one live lookup: the URL and the field the daily check reads.

    This is the same address `latest_version` builds, called with
    `trust_env=False` because this machine's NO_PROXY holds `[::1]`, which
    `httpx`'s default environment reading cannot parse. The job's own client is
    built by the caller (spec 17's outbound guard is a later unit's job).
    """
    from townrecord.runtime.pypi import latest_version

    with httpx.Client(timeout=20.0, trust_env=False) as client:
        latest = latest_version(client)

    assert latest
    assert latest.split(".")[0].isdigit()
