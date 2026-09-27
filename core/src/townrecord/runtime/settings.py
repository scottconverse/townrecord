"""Settings for the private tool runtime (spec 8.9, 14.4).

TownRecord owns its tools. yt-dlp lives in its own venv under an app-data root
the user chose, so capture never depends on what happens to be installed for
the system Python. That is the failure this unit answers: the allow-listed
child environment of :mod:`townrecord.proc` has no ``APPDATA``, so a yt-dlp in
the user site is invisible to the child and the run ends with
``No module named yt_dlp``.

Every value can be changed with a TOWNRECORD_RUNTIME_* environment variable,
the same way the jobs and capture settings are read.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

#: The tool this package manages. One name, used for the folder and the pin.
TOOL_NAME = "yt-dlp"

#: The module that name runs as. `python -m yt_dlp --version` is how the venv
#: is asked which version it holds (spec 8.9).
TOOL_MODULE = "yt_dlp"

#: The folder under the app-data root that holds the private runtimes.
RUNTIMES_FOLDER = "runtimes"

#: The file that names the active version and the one before it (spec 8.9).
POINTER_NAME = "current.json"

#: The file that keeps two updates from running at the same time.
LOCK_NAME = "update.lock"

#: Where the latest release is read from. PyPI's JSON API, checked live on
#: 2026-09-27: a 200 with ``info.version = "2026.8.19"``.
PYPI_JSON_URL = "https://pypi.org/pypi/{tool}/json"

#: How long `uv venv` and `uv pip install` may take together.
DEFAULT_INSTALL_TIMEOUT_S = 900.0

#: How long the venv may take to say which version it holds.
DEFAULT_VERSION_TIMEOUT_S = 60.0

#: How old a lock file may be before it is read as left behind by a crash.
DEFAULT_LOCK_STALE_S = 7200.0


def _number(source: Mapping[str, str], name: str, default: float) -> float:
    """Read a positive number from the environment, or keep the default."""
    text = source.get(name, "").strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class RuntimeSettings:
    """The waits one runtime install or update runs under."""

    install_timeout_s: float = DEFAULT_INSTALL_TIMEOUT_S
    version_timeout_s: float = DEFAULT_VERSION_TIMEOUT_S
    lock_stale_s: float = DEFAULT_LOCK_STALE_S

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RuntimeSettings:
        """Build settings from the TOWNRECORD_RUNTIME_* environment variables."""
        source = os.environ if env is None else env
        return cls(
            install_timeout_s=_number(
                source, "TOWNRECORD_RUNTIME_INSTALL_TIMEOUT_S", DEFAULT_INSTALL_TIMEOUT_S
            ),
            version_timeout_s=_number(
                source, "TOWNRECORD_RUNTIME_VERSION_TIMEOUT_S", DEFAULT_VERSION_TIMEOUT_S
            ),
            lock_stale_s=_number(source, "TOWNRECORD_RUNTIME_LOCK_STALE_S", DEFAULT_LOCK_STALE_S),
        )
