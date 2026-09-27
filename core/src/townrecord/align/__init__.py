"""Aligning a transcript with an agenda (spec 10.1, 10.2).

Agenda items and transcript segments go in; item boundaries come out, each
one naming the method that produced it. The two modules are:

* :mod:`townrecord.align.identifiers` reads ordinance and resolution numbers
  and agenda item numbers out of agenda titles and out of speech.
* :mod:`townrecord.align.align` measures the video's offset from the agenda's
  times, takes the spoken transitions, and falls back to no boundary with a
  reason.

Nothing here touches a database or the network. Every function is a pure
function of its arguments.
"""

from __future__ import annotations

from townrecord.align.align import (
    HTML_AGENDA_TIME,
    NO_ALIGNMENT,
    SPOKEN_TRANSITION,
    AlignedItem,
    AlignmentResult,
    AlignmentSettings,
    OffsetAnchor,
    OffsetEstimate,
    align_agenda_with_transcript,
)
from townrecord.align.identifiers import (
    ORDINANCE,
    RESOLUTION,
    Identifier,
    ItemReference,
    find_identifiers,
    find_item_references,
    significant_words,
    words_of,
)

__all__ = [
    "HTML_AGENDA_TIME",
    "NO_ALIGNMENT",
    "ORDINANCE",
    "RESOLUTION",
    "SPOKEN_TRANSITION",
    "AlignedItem",
    "AlignmentResult",
    "AlignmentSettings",
    "Identifier",
    "ItemReference",
    "OffsetAnchor",
    "OffsetEstimate",
    "align_agenda_with_transcript",
    "find_identifiers",
    "find_item_references",
    "significant_words",
    "words_of",
]
