"""The private tool runtime of spec 8.9 and 14.4.

TownRecord installs and manages its own tools instead of hoping they are on
the machine. Each one gets its own venv under an app-data root the user chose,
one folder per version, and the job that needs it runs that venv. yt-dlp is
the first tool and TextFlowKit, the local transcriber of spec 8.5, is the
second; :mod:`townrecord.runtime.tools` is where the difference between them
lives. The daily update of spec 8.9 installs a new release beside the current
one, tests it against one known video, and switches only when the test passes;
the version that was active stays installed, so a rollback is one pointer swap.

`register` wires the `runtime_update` job kind so the schedule of spec 16.2 can
enqueue it -- once per tool.
"""

from __future__ import annotations

from .job import JOB_KIND, LANE, RuntimeUpdate, register
from .lock import UpdateInProgress, UpdateLock
from .manager import (
    InstallResult,
    Probe,
    RollbackUnavailable,
    RuntimeInstallFailed,
    RuntimeManager,
    RuntimeNotInstalled,
    RuntimeToolMissing,
    UpdateOutcome,
)
from .pointer import Installed, Pointer, PointerUnreadable, read_pointer, write_pointer
from .pypi import RuntimeLookupFailed, latest_version
from .settings import (
    TEXTFLOWKIT_PROGRAM,
    TEXTFLOWKIT_TOOL,
    TOOL_MODULE,
    TOOL_NAME,
    RuntimeSettings,
)
from .tools import TOOL_SPECS, ToolSpec, UnknownTool, tool_spec
from .versions import is_newer, same_version, version_key

__all__ = [
    "JOB_KIND",
    "LANE",
    "TEXTFLOWKIT_PROGRAM",
    "TEXTFLOWKIT_TOOL",
    "TOOL_MODULE",
    "TOOL_NAME",
    "TOOL_SPECS",
    "InstallResult",
    "Installed",
    "Pointer",
    "PointerUnreadable",
    "Probe",
    "RollbackUnavailable",
    "RuntimeInstallFailed",
    "RuntimeLookupFailed",
    "RuntimeManager",
    "RuntimeNotInstalled",
    "RuntimeSettings",
    "RuntimeToolMissing",
    "RuntimeUpdate",
    "ToolSpec",
    "UnknownTool",
    "UpdateInProgress",
    "UpdateLock",
    "UpdateOutcome",
    "is_newer",
    "latest_version",
    "read_pointer",
    "register",
    "same_version",
    "tool_spec",
    "version_key",
    "write_pointer",
]
