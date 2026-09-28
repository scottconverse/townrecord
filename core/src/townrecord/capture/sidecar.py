"""What the info.json sidecar says about a capture (spec 8.6, 8.7).

The sidecar is the only witness to two facts the transcript row needs: who
made the captions, and how long the video is. So a capture without its sidecar
is never a silent success; the job records the missing sidecar and stops
(spec 8.6, "A missing info.json sidecar is recorded with its reason").

Spec 8.7 tests three signals for a revision, and two of the three are here: the
hash is the caption file's own bytes, and the other two -- the caption revision
time and the duration -- are what this module reads. The revision time is the
one signal a YouTube sidecar usually leaves unsaid: the track entries yt-dlp
writes carry no timestamp, so it returns None and the test moves on to the
duration. That is the honest answer of a source that states nothing, and it is
why the change test never depends on this signal alone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: The transcript origins of migration 0005 that the sidecar can decide.
PUBLISHER_CAPTIONS = "publisher_captions"
AUTO_CAPTIONS = "auto_captions"

#: The language the job asks for (spec 8.3: --sub-langs en).
LANGUAGE = "en"

#: The names a caption track's own revision time is stated under. Two
#: spellings, because this is a fact of somebody else's document: yt-dlp's
#: track entries carry neither today, and an extractor that states one is the
#: case this exists for. A revision time is read as the source wrote it and
#: compared as text, so restating the same instant in another form is read as
#: the change it is rather than parsed into a moment that hides it.
REVISION_KEYS: tuple[str, ...] = ("last_modified", "modified")


def _has_language(tracks: Any) -> bool:
    """Return True when a track table holds the language the job asked for."""
    return isinstance(tracks, Mapping) and LANGUAGE in tracks


def caption_track(info: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the English track entry the capture read, or None.

    The publisher's track is preferred to the automatic one, which is the same
    order :func:`caption_origin` decides in: a video that has both is captured
    from ``subtitles``, so that is the track whose revision time matters.
    """
    for table in ("subtitles", "automatic_captions"):
        tracks = info.get(table)
        if not _has_language(tracks):
            continue
        entries = tracks[LANGUAGE]
        if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes)) and entries:
            first = entries[0]
            if isinstance(first, Mapping):
                return dict(first)
    return None


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


def revision_at(info: Mapping[str, Any]) -> str | None:
    """Return when the caption track was last revised, or None.

    Spec 8.7's second signal. It is read off the track entry the capture read,
    under the names of :data:`REVISION_KEYS`, and returned as text: the caller
    compares it with what the last check saw, and two spellings that differ are
    two states of the document however equal the instants they name.

    None means the source states no revision time, which is the usual answer of
    a YouTube sidecar. Nothing is inferred to fill the gap -- a signal nobody
    states cannot be the signal that changed (spec 16.3).
    """
    entry = caption_track(info)
    if entry is None:
        return None
    for key in REVISION_KEYS:
        value = entry.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return None
