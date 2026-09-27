"""Runtime settings for the core service (spec 13.1)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__

#: The API listens on the loopback address unless the user turns on network serving.
DEFAULT_HOST = "127.0.0.1"

#: The port the user can change in settings.
DEFAULT_PORT = 8190


def default_db_path() -> Path:
    """Return the database path used when nothing else is configured."""
    return Path.home() / ".townrecord" / "townrecord.db"


@dataclass(frozen=True)
class Settings:
    """Settings for one run of the core service."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    db_path: Path = field(default_factory=default_db_path)
    version: str = __version__

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from the TOWNRECORD_* environment variables."""
        source = os.environ if env is None else env
        port_text = source.get("TOWNRECORD_PORT", "").strip()
        try:
            port = int(port_text) if port_text else DEFAULT_PORT
        except ValueError:
            port = DEFAULT_PORT
        db_text = source.get("TOWNRECORD_DB", "").strip()
        return cls(
            host=source.get("TOWNRECORD_HOST", "").strip() or DEFAULT_HOST,
            port=port,
            db_path=Path(db_text) if db_text else default_db_path(),
        )
