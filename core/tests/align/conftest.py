"""Shared pieces for the alignment tests (spec 10.1, 10.2).

Two kinds of fixture are used here, and they are kept apart on purpose:

* Hand-written agendas and transcripts in the test files themselves, small
  enough to read, one behaviour each.
* The recorded evidence of the September 8, 2026 Longmont meeting, read in
  place from the oversight repository rather than copied in. The srv3 caption
  track is a multi-megabyte recording of a real meeting and the brief for this
  unit says not to copy it into the repository, so :data:`CAPTIONS_16805` is
  skipped when it is not on this machine.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from townrecord.adapters.base import (
    VIDEO_TIME_MISSING,
    VIDEO_TIME_OUTSIDE_VIDEO,
    VIDEO_TIME_USABLE,
    AgendaItem,
)
from townrecord.captions.parse import Segment

#: The recorded fixtures of the oversight repository. Override with
#: ``TOWNRECORD_EVIDENCE`` when they live somewhere else.
EVIDENCE_FOLDER = Path(
    os.environ.get(
        "TOWNRECORD_EVIDENCE",
        Path(__file__).resolve().parents[4] / "townrecord-oversight" / "evidence" / "fixtures",
    )
)

#: The September 8, 2026 City Council regular session HTML agenda (16805).
AGENDA_16805 = EVIDENCE_FOLDER / "primegov" / "longmont-html-agenda-16805.html"

#: The June 2, 2026 City Council study session HTML agenda (16095). It carries
#: no ``data-videolocation`` at all.
AGENDA_16095 = EVIDENCE_FOLDER / "primegov" / "longmont-html-agenda-16095.html"

#: The srv3 caption track of video 3qfQAkAAC9U, the September 8, 2026 meeting.
CAPTIONS_16805 = EVIDENCE_FOLDER / "youtube" / "captions" / "3qfQAkAAC9U.en.srv3"

#: The measured length of that video, from yt-dlp's metadata.
VIDEO_DURATION_S = 14407

#: The agenda times that are placeholders rather than times: every one of them
#: is longer than the video. Spec 10.2's rule that a time outside the video is
#: unusable is what keeps them out of an answer.
PLACEHOLDER_SECONDS = (585015, 585021, 658102, 658993)

#: The tolerance and anchor count the September 8, 2026 video was measured with.
MEASURED_TOLERANCE_S = 30.0
MEASURED_MIN_ANCHORS = 3


def needs(*paths: Path) -> pytest.MarkDecorator:
    """Skip a test when a recorded fixture is not on this machine."""
    missing = ", ".join(str(path) for path in paths if not path.exists())
    return pytest.mark.skipif(bool(missing), reason=f"recorded fixture not present: {missing}")


def segment(start_s: float, text: str, *, seconds: float = 2.0) -> Segment:
    """One line of captioned speech at a time, for a hand-written transcript."""
    start_ms = round(start_s * 1000)
    return Segment(start_ms=start_ms, end_ms=start_ms + round(seconds * 1000), text=text)


def agenda_item(
    number: str,
    title: str,
    video_seconds: int | None = None,
    *,
    video_duration_s: int = VIDEO_DURATION_S,
) -> AgendaItem:
    """One agenda item, with the status the adapter would have given it."""
    if video_seconds is None:
        status = VIDEO_TIME_MISSING
    elif video_seconds > video_duration_s:
        status = VIDEO_TIME_OUTSIDE_VIDEO
    else:
        status = VIDEO_TIME_USABLE
    return AgendaItem(
        number=number, title=title, video_seconds=video_seconds, video_time_status=status
    )
