"""The size and length limits of spec 8.3.

"Size and length limits: default 8 hours and 500 MB. Larger meetings need the
user's confirmation, and a refusal is recorded with its reason."

The length comes from the info.json yt-dlp wrote, because that is the duration
of the video itself and not of the listing that may have carried none. The size
is what the capture actually put in the work folder. A capture over either
limit is refused with a sentence the user can act on; the confirmation that
lifts the refusal is a later unit.
"""

from __future__ import annotations

from pathlib import Path

from .settings import CaptureSettings


def format_duration(seconds: float) -> str:
    """Return a duration as ``4h 43m``, for a sentence a person reads."""
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def format_size(count: int) -> str:
    """Return a byte count in MB, for a sentence a person reads."""
    return f"{count / (1024 * 1024):g} MB"


def folder_bytes(folder: str | Path) -> int:
    """Return the total size of the files in a folder, subfolders included."""
    total = 0
    for path in Path(folder).rglob("*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def refusal(
    *, duration_s: float | None, capture_bytes: int, settings: CaptureSettings
) -> str | None:
    """Return the reason to refuse this capture, or None when it may proceed.

    A duration the info.json did not state is not a refusal: the job cannot
    invent one, and the size still guards the capture.
    """
    if duration_s is not None and duration_s > settings.max_duration_s:
        return (
            f"Refused: the meeting runs {format_duration(duration_s)}, over the "
            f"{format_duration(settings.max_duration_s)} limit. Capturing it needs "
            f"your confirmation."
        )
    if capture_bytes > settings.max_capture_bytes:
        return (
            f"Refused: the capture is {format_size(capture_bytes)}, over the "
            f"{format_size(settings.max_capture_bytes)} limit. Capturing it needs "
            f"your confirmation."
        )
    return None
