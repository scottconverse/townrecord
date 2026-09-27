"""What the info.json sidecar says about a capture (spec 8.6, 8.7).

The sidecar is the only witness to two facts the transcript row needs: who
made the captions, and how long the video is. So a capture without its sidecar
is never a silent success; the job records the missing sidecar and stops
(spec 8.6, "A missing info.json sidecar is recorded with its reason").
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The transcript origins of migration 0005 that the sidecar can decide.
PUBLISHER_CAPTIONS = "publisher_captions"
AUTO_CAPTIONS = "auto_captions"

#: The language the job asks for (spec 8.3: --sub-langs en).
LANGUAGE = "en"


def _has_language(tracks: Any) -> bool:
    """Return True when a track table holds the language the job asked for."""
    return isinstance(tracks, Mapping) and LANGUAGE in tracks


def caption_origin(info: Mapping[str, Any]) -> str | None:
    """Return who made the captions, or None when the sidecar does not say.

    The rule, in the words of the schema comment and of the coordinator's read
    of a real Longmont sidecar: ``subtitles`` holds the tracks the publisher
    uploaded, and ``automatic_captions`` holds the ones YouTube made. So ``en``
    in ``subtitles`` means publisher captions, and ``en`` only in
    ``automatic_captions`` means auto captions. A sidecar with neither cannot
    decide, and the job does not guess: it stops with a reason.
    """
    if _has_language(info.get("subtitles")):
        return PUBLISHER_CAPTIONS
    if _has_language(info.get("automatic_captions")):
        return AUTO_CAPTIONS
    return None


def duration_s(info: Mapping[str, Any]) -> float | None:
    """Return the duration in seconds the sidecar states, or None.

    A value that is not a number is read as no value at all: the length limit
    of spec 8.3 then has nothing to refuse on, rather than refusing on a
    number TownRecord invented.
    """
    value = info.get("duration")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value >= 0 else None
