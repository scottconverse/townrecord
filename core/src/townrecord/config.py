"""Runtime settings for the core service (spec 13.1)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

from . import __version__

#: The API listens on the loopback address unless the user turns on network serving.
DEFAULT_HOST = "127.0.0.1"

#: The port the user can change in settings.
DEFAULT_PORT = 8190

#: The local time the daily scan runs at (spec 16.2). The user chooses the
#: time; this is the ordinary early-morning one they get until they do.
DEFAULT_DAILY_TIME = time(6, 0)


def default_db_path() -> Path:
    """Return the database path used when nothing else is configured."""
    return Path.home() / ".townrecord" / "townrecord.db"


def default_storage_root() -> Path:
    """Return the artifact storage root used when nothing else is configured.

    The artifact rows hold a path relative to a root the user chose (migration
    0004), so the root is runtime state rather than a fact of the database. It
    sits next to the database by default.
    """
    return Path.home() / ".townrecord" / "storage"


def default_runtime_root() -> Path:
    """Return the app-data root the private tool runtimes live under.

    ``RuntimeManager`` builds ``<root>/runtimes/<tool>`` under it (spec 8.9,
    14.4). It is the app-data folder itself and not the ``runtimes`` folder
    inside it, because the manager owns that name. It sits next to the database
    by default, and it must never be a folder inside the application.
    """
    return Path.home() / ".townrecord"


def parse_daily_time(text: str) -> time:
    """Read a daily time written as HH:MM. Anything else is refused."""
    hour, _, minute = str(text).strip().partition(":")
    if not hour.isdigit() or not minute.isdigit():
        raise ValueError(f"A daily time is written HH:MM, not {text!r}.")
    return time(int(hour), int(minute))


@dataclass(frozen=True)
class Settings:
    """Settings for one run of the core service."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    db_path: Path = field(default_factory=default_db_path)
    storage_root: Path = field(default_factory=default_storage_root)
    #: The app-data root the private tool runtimes are installed under
    #: (spec 8.9). It is a setting and never a guess, the same way the storage
    #: root is: a runtime in the wrong folder is a runtime nobody asked for.
    runtime_root: Path = field(default_factory=default_runtime_root)
    #: The IANA name of the area's time zone, such as America/Denver. Empty
    #: means the user has not named one, and the daily schedule says so plainly
    #: rather than picking a zone for them (spec 16.2, rule D).
    time_zone: str = ""
    #: The local time of the daily scan, in `time_zone` (spec 16.2).
    daily_time: time = DEFAULT_DAILY_TIME
    #: The one known video the yt-dlp update is tested against (spec 8.9).
    #: Empty means no test video is configured, and the daily check says so
    #: instead of choosing a video for the user.
    test_video_url: str = ""
    version: str = __version__

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from the TOWNRECORD_* environment variables."""
        source = os.environ if env is None else env
        db_text = source.get("TOWNRECORD_DB", "").strip()
        storage_text = source.get("TOWNRECORD_STORAGE", "").strip()
        runtime_text = source.get("TOWNRECORD_RUNTIME_ROOT", "").strip()
        return cls(
            host=source.get("TOWNRECORD_HOST", "").strip() or DEFAULT_HOST,
            port=_port(source),
            db_path=Path(db_text) if db_text else default_db_path(),
            storage_root=Path(storage_text) if storage_text else default_storage_root(),
            runtime_root=Path(runtime_text) if runtime_text else default_runtime_root(),
            time_zone=source.get("TOWNRECORD_TIME_ZONE", "").strip(),
            daily_time=_daily_time(source),
            test_video_url=source.get("TOWNRECORD_TEST_VIDEO", "").strip(),
        )


def _port(source: Mapping[str, str]) -> int:
    """Read the port, or the default when it is not a usable number."""
    text = source.get("TOWNRECORD_PORT", "").strip()
    try:
        return int(text) if text else DEFAULT_PORT
    except ValueError:
        return DEFAULT_PORT


def _daily_time(source: Mapping[str, str]) -> time:
    """Read the daily time, or the default when it is not written HH:MM."""
    text = source.get("TOWNRECORD_DAILY_TIME", "").strip()
    if not text:
        return DEFAULT_DAILY_TIME
    try:
        return parse_daily_time(text)
    except ValueError:
        return DEFAULT_DAILY_TIME
