"""Which JavaScript runtime yt-dlp is told to use (spec 8.3, 8.9, 14.4).

YouTube's pages need a JavaScript runtime, and yt-dlp is told which one to use
with ``--js-runtimes``. A user's computer may have no Node at all, and asking
the user to install one is not what the private runtime of spec 8.9 is for. So
the runtime is installed *beside yt-dlp*: ``deno`` is a project on PyPI, so one
``uv pip install`` puts the runtime in the same venv as the tool that needs it,
and the interpreter of that venv always has its runtime in the folder next to
it. Nothing is looked for on PATH, so what yt-dlp is told does not depend on
what else happens to be installed on the machine.

The value of the option is ``RUNTIME[:PATH]``. yt-dlp splits the value at the
first colon into a name and a path, so a runtime that is not on PATH is spelled
``deno:<full path to the binary>``: on Windows a bare ``C:\\...`` would be read
as a runtime named ``C``. That grammar was read out of yt-dlp's own source
(``yt_dlp/__init__.py`` and ``yt_dlp/utils/_jsruntime.py``), not guessed.

Node is still the fallback, handed over as the bare word ``node``: a bare name
is one yt-dlp searches for itself, on PATH and beside its own interpreter, so a
machine that does have Node keeps working when deno is somehow missing. A bare
program name is never treated as an interpreter of a private venv, because it
names no folder: the fallback is what a caller that has no venv gets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .tools import JAVASCRIPT_COMPANION, program_name

__all__ = [
    "FALLBACK_RUNTIME",
    "FALLBACK_SOURCE",
    "PRIVATE_RUNTIME",
    "PRIVATE_SOURCE",
    "JavaScriptRuntime",
    "in_private_runtime",
    "resolve",
]

#: The name of the runtime the private venv installs, and the program it
#: provides. The name is the value yt-dlp reads, and the pin that installs it
#: lives with the tool spec in :mod:`townrecord.runtime.tools`.
PRIVATE_RUNTIME = JAVASCRIPT_COMPANION.program

#: The runtime named when no private runtime is installed. It is a bare name
#: on purpose: yt-dlp searches for that itself.
FALLBACK_RUNTIME = "node"

#: Where a runtime came from, for the provenance a capture writes down.
PRIVATE_SOURCE = "the private runtime"
FALLBACK_SOURCE = "the fallback"


@dataclass(frozen=True)
class JavaScriptRuntime:
    """One JavaScript runtime, and how it is spelled to yt-dlp."""

    #: The name yt-dlp knows the runtime by.
    name: str
    #: The full path of the runtime's program, or "" when there is none.
    path: str = ""
    #: Where this runtime came from, for a capture's provenance.
    source: str = FALLBACK_SOURCE

    @property
    def argument(self) -> str:
        """The value of ``--js-runtimes`` for this runtime.

        ``NAME:PATH`` when there is a path, and the bare name otherwise. yt-dlp
        splits at the first colon, so the path is spelled exactly as it is.
        """
        return f"{self.name}:{self.path}" if self.path else self.name


def program_beside(interpreter: str | Path, program: str = PRIVATE_RUNTIME) -> Path | None:
    """Return a program sitting next to an interpreter, or None.

    A venv keeps its interpreter and its console scripts in one folder, so the
    runtime of the private venv is the file beside the interpreter that venv
    runs.
    """
    folder = Path(interpreter).parent
    found = folder / program_name(program)
    return found if found.is_file() else None


def in_private_runtime(
    interpreter: str | None, *, runtime: str = PRIVATE_RUNTIME
) -> JavaScriptRuntime | None:
    """Return the JavaScript runtime beside a private interpreter, or None.

    A bare program name is not an interpreter of a venv: there is no folder
    beside it that TownRecord put a runtime into, and whatever a bare name
    finds on this machine is not something an argument list may depend on. So
    only an absolute interpreter path is looked beside, and a caller that names
    its interpreter as a plain word gets None.
    """
    if not interpreter:
        return None
    if not Path(interpreter).is_absolute():
        return None
    found = program_beside(interpreter, runtime)
    if found is None:
        return None
    return JavaScriptRuntime(name=runtime, path=str(found), source=PRIVATE_SOURCE)


def resolve(interpreter: str | None = None, *, runtime: str = PRIVATE_RUNTIME) -> JavaScriptRuntime:
    """Return the runtime yt-dlp is told to use for one interpreter.

    The private runtime first, because TownRecord installed it itself and knows
    it is there. The bare fallback name second. A machine with neither deno nor
    node asks for the bot check of spec 8.3, which is a deferral and not a
    failure, so this never has to raise.
    """
    found = in_private_runtime(interpreter, runtime=runtime)
    return found if found is not None else JavaScriptRuntime(name=FALLBACK_RUNTIME)
