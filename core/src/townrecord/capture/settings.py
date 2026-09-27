"""Settings for the caption capture job (spec 8.3, 8.7).

Every value can be changed with a TOWNRECORD_CAPTURE_* environment variable,
the same way the jobs settings of spec 16.1 are read.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from ..stt.textflowkit import DEFAULT_LANGUAGE, DEFAULT_MODEL

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

#: "Local transcription only" (spec 8.10). Off by default: captions come first
#: because they are small (spec 8.3), and a user who wants every meeting
#: transcribed on this machine turns this on.
DEFAULT_LOCAL_TRANSCRIPTION_ONLY = False

#: How long one audio download may take before it is killed. The audio of a long
#: meeting is a large file over a slow link, so this is far longer than the
#: caption run of spec 8.3 needs.
DEFAULT_DOWNLOAD_TIMEOUT_S = 1800.0

#: How long the transcriber may take to say which version it holds.
DEFAULT_VERSION_TIMEOUT_S = 60.0

#: The spellings that turn a boolean setting on, when it is read from the
#: environment. Anything else is off, which is the safe direction for a switch
#: that changes what is downloaded.
_TRUE_SPELLINGS = frozenset({"1", "true", "yes", "on"})


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


def _flag(source: Mapping[str, str], name: str, default: bool) -> bool:
    """Read a yes/no setting from the environment, or keep the default."""
    text = source.get(name, "").strip().lower()
    if not text:
        return default
    return text in _TRUE_SPELLINGS


@dataclass(frozen=True)
class CaptureSettings:
    """The limits and the waits one caption capture runs under."""

    max_duration_s: int = DEFAULT_MAX_DURATION_S
    max_capture_bytes: int = DEFAULT_MAX_CAPTURE_BYTES
    process_timeout_s: float = DEFAULT_PROCESS_TIMEOUT_S
    not_ready_retry_s: float = DEFAULT_NOT_READY_RETRY_S
    rate_limit_retry_s: float = DEFAULT_RATE_LIMIT_RETRY_S
    #: Spec 8.10: when this is on, the caption command is never run and every
    #: meeting is transcribed on this machine instead.
    local_transcription_only: bool = DEFAULT_LOCAL_TRANSCRIPTION_ONLY

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
            local_transcription_only=_flag(
                source,
                "TOWNRECORD_CAPTURE_LOCAL_TRANSCRIPTION_ONLY",
                DEFAULT_LOCAL_TRANSCRIPTION_ONLY,
            ),
        )


@dataclass(frozen=True)
class TranscribeSettings:
    """The model and the waits one local transcription runs under (spec 8.5).

    The model is ``small`` by default: spec 8.10 trades accuracy for time on the
    machine the user already has. The two timeouts are for the two children the
    job runs, and the transcription's own limit is not here: it is computed from
    the length of the recording by
    :func:`~townrecord.stt.textflowkit.transcription_timeout_s`.
    """

    model: str = DEFAULT_MODEL
    language: str = DEFAULT_LANGUAGE
    download_timeout_s: float = DEFAULT_DOWNLOAD_TIMEOUT_S
    version_timeout_s: float = DEFAULT_VERSION_TIMEOUT_S
    rate_limit_retry_s: float = DEFAULT_RATE_LIMIT_RETRY_S

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> TranscribeSettings:
        """Build settings from the TOWNRECORD_TRANSCRIBE_* environment variables."""
        source = os.environ if env is None else env
        return cls(
            model=source.get("TOWNRECORD_TRANSCRIBE_MODEL", "").strip() or DEFAULT_MODEL,
            language=source.get("TOWNRECORD_TRANSCRIBE_LANGUAGE", "").strip() or DEFAULT_LANGUAGE,
            download_timeout_s=_number(
                source, "TOWNRECORD_TRANSCRIBE_DOWNLOAD_TIMEOUT_S", DEFAULT_DOWNLOAD_TIMEOUT_S
            ),
            version_timeout_s=_number(
                source, "TOWNRECORD_TRANSCRIBE_VERSION_TIMEOUT_S", DEFAULT_VERSION_TIMEOUT_S
            ),
            rate_limit_retry_s=_number(
                source, "TOWNRECORD_TRANSCRIBE_RATE_LIMIT_RETRY_S", DEFAULT_RATE_LIMIT_RETRY_S
            ),
        )
