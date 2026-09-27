"""Settings for the caption capture job (spec 8.3, 8.7).

Every value can be changed with a TOWNRECORD_CAPTURE_* environment variable,
the same way the jobs settings of spec 16.1 are read.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

#: The length limit of spec 8.3: eight hours. A longer meeting is not captured
#: until the user confirms it.
DEFAULT_MAX_DURATION_S = 8 * 3600

#: The size limit of spec 8.3: 500 MB. Captions are a fraction of that, so this
#: is a guard against something other than captions arriving.
DEFAULT_MAX_CAPTURE_BYTES = 500 * 1024 * 1024

#: How long one yt-dlp run may take before it is killed. A whole meeting's
#: captions are small; a run that takes longer than this is stuck.
DEFAULT_PROCESS_TIMEOUT_S = 900.0

#: How long to wait before looking at an upcoming or live video again.
DEFAULT_NOT_READY_RETRY_S = 3600.0

#: How long to wait after HTTP 429. This is the paced retry of spec 8.3, and it
#: is the whole answer to a rate limit: audio is never downloaded because of it.
DEFAULT_RATE_LIMIT_RETRY_S = 900.0


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
class CaptureSettings:
    """The limits and the waits one caption capture runs under."""

    max_duration_s: int = DEFAULT_MAX_DURATION_S
    max_capture_bytes: int = DEFAULT_MAX_CAPTURE_BYTES
    process_timeout_s: float = DEFAULT_PROCESS_TIMEOUT_S
    not_ready_retry_s: float = DEFAULT_NOT_READY_RETRY_S
    rate_limit_retry_s: float = DEFAULT_RATE_LIMIT_RETRY_S

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CaptureSettings:
        """Build settings from the TOWNRECORD_CAPTURE_* environment variables."""
        source = os.environ if env is None else env
        return cls(
            max_duration_s=int(
                _number(source, "TOWNRECORD_CAPTURE_MAX_DURATION_S", DEFAULT_MAX_DURATION_S)
            ),
            max_capture_bytes=int(
                _number(source, "TOWNRECORD_CAPTURE_MAX_BYTES", DEFAULT_MAX_CAPTURE_BYTES)
            ),
            process_timeout_s=_number(
                source, "TOWNRECORD_CAPTURE_PROCESS_TIMEOUT_S", DEFAULT_PROCESS_TIMEOUT_S
            ),
            not_ready_retry_s=_number(
                source, "TOWNRECORD_CAPTURE_NOT_READY_RETRY_S", DEFAULT_NOT_READY_RETRY_S
            ),
            rate_limit_retry_s=_number(
                source, "TOWNRECORD_CAPTURE_RATE_LIMIT_RETRY_S", DEFAULT_RATE_LIMIT_RETRY_S
            ),
        )
