"""The tools TownRecord owns (spec 8.9, 14.4).

Spec 8.9 gives every tool its own venv under the app-data root, and this unit
adds the second one: yt-dlp, which fetches the captions and the audio, and
TextFlowKit, which transcribes the audio of a meeting on this machine
(spec 8.5). The two are managed the same way -- their own folder, their own
pinned version, their own pointer and their own rollback -- and they differ in
only two things:

* the name, which is the folder under the runtimes root and the pin handed to
  ``uv pip install`` (the PyPI project name), and
* how the venv is asked which version it holds: yt-dlp is run as
  ``python -m yt_dlp --version`` and TextFlowKit as the program it installs.

Nothing here reads a version from the network or from a file: a spec is a
constant, and the version that is real is the one the venv reports (spec 8.9).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .settings import TEXTFLOWKIT_PROGRAM, TEXTFLOWKIT_TOOL, TOOL_MODULE, TOOL_NAME


class UnknownTool(RuntimeError):
    """TownRecord was asked for a tool it does not manage."""


def scripts_folder(venv: str | Path) -> Path:
    """Return the folder a venv puts its executables in, on this system."""
    return Path(venv) / ("Scripts" if os.name == "nt" else "bin")


def program_in(venv: str | Path, program: str) -> Path:
    """Return the path of a console script inside a venv, on this system."""
    folder = scripts_folder(venv)
    return folder / f"{program}.exe" if os.name == "nt" else folder / program


def python_in(venv: str | Path) -> Path:
    """Return the path of a venv's own interpreter, on this system."""
    return scripts_folder(venv) / ("python.exe" if os.name == "nt" else "python")


@dataclass(frozen=True)
class ToolSpec:
    """One tool the private runtime installs and runs."""

    #: The name of the tool in every place it is named: the folder under the
    #: runtimes root, the folder that holds the pointer, and the pin.
    tool: str
    #: The module ``python -m <module> --version`` asks, when the tool is run
    #: as a module. None means the tool is asked through its own program.
    module: str | None
    #: The console script the tool installs into its venv. This is what a job
    #: runs when it runs the tool directly rather than through a module.
    program: str

    def version_argv(self, *, python: Path, venv: Path) -> list[str]:
        """Return the argument list that asks the venv which version it holds.

        The answer is what gets pinned (spec 8.9): the request is a wish, the
        venv is the fact.
        """
        if self.module is not None:
            return [str(python), "-m", self.module, "--version"]
        return [str(program_in(venv, self.program)), "--version"]


#: Every tool TownRecord manages, by the name it is known by.
TOOL_SPECS: dict[str, ToolSpec] = {
    TOOL_NAME: ToolSpec(tool=TOOL_NAME, module=TOOL_MODULE, program="yt-dlp"),
    TEXTFLOWKIT_TOOL: ToolSpec(tool=TEXTFLOWKIT_TOOL, module=None, program=TEXTFLOWKIT_PROGRAM),
}

__all__ = [
    "TOOL_SPECS",
    "ToolSpec",
    "UnknownTool",
    "program_in",
    "python_in",
    "scripts_folder",
    "tool_spec",
]


def tool_spec(tool: str) -> ToolSpec:
    """Return the spec of one managed tool, or raise :class:`UnknownTool`."""
    name = str(tool).strip()
    try:
        return TOOL_SPECS[name]
    except KeyError:
        known = ", ".join(sorted(TOOL_SPECS))
        raise UnknownTool(
            f"TownRecord does not manage a tool called {name!r}; it manages {known}."
        ) from None
