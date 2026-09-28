"""Caption capture: the job of spec 8.2, 8.3, 8.6 and 8.7.

Captions come first because they are small (spec 8.3): about 136 KB for a
two-hour meeting against about 120 MB of audio. This package runs the yt-dlp
command, reads what it wrote, and stores the transcript. HTTP 429 never makes
audio this package's business: it is a paced retry (spec 8.3).

When the captions are missing or unusable the fallbacks of spec 8.4 are here
too, and they end in the `transcribe` job of spec 8.5, which downloads the
audio and transcribes it on this machine. Both jobs write their transcript
through the one function in `.store`, because spec 8.5 allows no second code
path.

`register` wires the caption handler into a registry together with the storage
root the user chose, which is the one setting neither job can read for itself.
`register_transcribe` wires the audio one, together with the paths of the two
programs it runs out of the private runtime of spec 8.9.

`register_recheck` wires the third job of this package, the recheck of spec 8.7.
It shares the storage root with the capture, because it fetches into a folder
beside the one the capture uses, and it is its own kind: a capture fetches for
the first time and may fall back to audio, a recheck fetches again and may
revise, and neither should be able to do the other's work by accident.
"""

from __future__ import annotations

from pathlib import Path

from ..jobs.registry import Registry, default_registry
from . import transcribe
from .command import JOB_KIND, LANE, CaptureFailed, Runner, rate_limit_marker
from .job import CaptionCapture
from .recheck import (
    RECHECK_JOB_KIND,
    CaptionRecheck,
    next_recheck_at,
    register_recheck,
    request_recheck,
)
from .settings import CaptureSettings, TranscribeSettings
from .store import insert_transcript_with_segments
from .transcribe import TranscribeAudio

__all__ = [
    "JOB_KIND",
    "LANE",
    "RECHECK_JOB_KIND",
    "TRANSCRIBE_JOB_KIND",
    "TRANSCRIBE_LANE",
    "CaptionCapture",
    "CaptionRecheck",
    "CaptureFailed",
    "CaptureSettings",
    "Runner",
    "TranscribeAudio",
    "TranscribeSettings",
    "insert_transcript_with_segments",
    "next_recheck_at",
    "rate_limit_marker",
    "register",
    "register_recheck",
    "register_transcribe",
    "request_recheck",
]

#: The kind and the lane of the transcription job (spec 8.5, 16.1). The kind is
#: `transcribe`, which is the spelling ``jobs.settings.DEFAULT_KIND_LIMITS``
#: already limits to one at a time, so the limit needs no setting of its own.
TRANSCRIBE_JOB_KIND = transcribe.JOB_KIND
TRANSCRIBE_LANE = transcribe.LANE


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


def register_transcribe(
    *,
    storage_root: str | Path,
    registry: Registry | None = None,
    settings: TranscribeSettings | None = None,
    runner: Runner | None = None,
    interpreter: str | None = None,
    textflowkit_program: str | None = None,
) -> TranscribeAudio:
    """Register the local transcription job and return the handler.

    Two programs are run out of the private runtime of spec 8.9: yt-dlp, which
    downloads the audio, and TextFlowKit, which transcribes it. Both paths are
    injected rather than looked up here, so this module does not depend on
    where the runtime happens to put them; the defaults are this process's
    interpreter and the console script's name on PATH.
    """
    handler = TranscribeAudio(
        storage_root=Path(storage_root),
        settings=settings or TranscribeSettings(),
        runner=runner,
        ytdlp_interpreter=interpreter,
        textflowkit_program=textflowkit_program,
    )
    (default_registry if registry is None else registry).register(
        transcribe.JOB_KIND, handler, lane=transcribe.LANE
    )
    return handler
