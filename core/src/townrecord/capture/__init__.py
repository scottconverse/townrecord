"""Caption capture: the job of spec 8.2, 8.3, 8.6 and 8.7.

Captions come first because they are small (spec 8.3): about 136 KB for a
two-hour meeting against about 120 MB of audio. This package runs the yt-dlp
command, reads what it wrote, and stores the transcript. Audio is not its
business, and HTTP 429 never makes it audio's business.

`register` wires the handler into a registry together with the storage root the
user chose, which is the one setting this job cannot read for itself.
"""

from __future__ import annotations

from pathlib import Path

from ..jobs.registry import Registry, default_registry
from .command import JOB_KIND, LANE, CaptureFailed, Runner, rate_limit_marker
from .job import CaptionCapture, insert_transcript_with_segments
from .settings import CaptureSettings

__all__ = [
    "JOB_KIND",
    "LANE",
    "CaptionCapture",
    "CaptureFailed",
    "CaptureSettings",
    "Runner",
    "insert_transcript_with_segments",
    "rate_limit_marker",
    "register",
]


def register(
    *,
    storage_root: str | Path,
    registry: Registry | None = None,
    settings: CaptureSettings | None = None,
    runner: Runner | None = None,
    interpreter: str | None = None,
) -> CaptionCapture:
    """Register the caption capture job and return the handler.

    The storage root is required and is never defaulted: it is the path the
    user chose (spec 8.6), and a job that invented one would write a capture
    somewhere the user never agreed to.
    """
    handler = CaptionCapture(
        storage_root=Path(storage_root),
        settings=settings or CaptureSettings(),
        runner=runner,
        interpreter=interpreter,
    )
    (default_registry if registry is None else registry).register(JOB_KIND, handler, lane=LANE)
    return handler
