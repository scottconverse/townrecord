"""Whether a video may be captured yet, and what to say when it may not.

Spec 8.2: "Skip upcoming and live videos, count them, and retry later. Treat
'unknown' as 'waiting for status metadata (will retry),' not as a failure."

The readiness word is the one written on the video row when the listing read
the publisher's status, and this module is only the words: for a word that
decides, the job reads it back and moves on. A word that decides nothing is
the case this module cannot settle, because a feed writes no status at all and
then nothing would ever change the row. So `wait_reason` returning the unknown
reason is exactly the row saying "nothing has decided this yet", and it is the
job's cue to ask spec 8.2's three steps itself
(:mod:`townrecord.video.youtube.status`) and write the answer down.

Waiting is not failing, so the job goes back in the queue with a plain reason
and a `run_after`, and `videos.capture_state` becomes 'skipped' for the two
words that mean the meeting has not happened yet. That word is what makes them
count: "the upcoming or live video that is left for a later pass", as the
migration that added the column puts it.
"""

from __future__ import annotations

from ..video.youtube.readiness import (
    READINESS_FINISHED,
    READINESS_LIVE,
    READINESS_UNKNOWN,
    READINESS_UPCOMING,
    READINESS_WAITING_REASON,
)

#: The plain reason for each word the job waits on.
WAIT_REASONS: dict[str, str] = {
    READINESS_UPCOMING: "upcoming, will retry",
    READINESS_LIVE: "live, will retry",
    READINESS_UNKNOWN: READINESS_WAITING_REASON,
}

#: The words the video row is marked 'skipped' for. 'unknown' is not one of
#: them: nothing has been decided about that video yet, so calling it skipped
#: would claim a decision the job did not make.
SKIP_STATES: frozenset[str] = frozenset({READINESS_UPCOMING, READINESS_LIVE})


def wait_reason(readiness_word: str | None) -> str | None:
    """Return the plain reason to wait, or None when capture may start.

    A word the table does not accept, or none at all, is read as 'unknown', so
    the caller must not capture something it knows nothing about. The unknown
    reason is also the caller's cue that nothing has decided this video yet and
    the status has to be asked for (spec 8.2).
    """
    word = (readiness_word or "").strip()
    if word == READINESS_FINISHED:
        return None
    return WAIT_REASONS.get(word, READINESS_WAITING_REASON)


def marks_skipped(readiness_word: str | None) -> bool:
    """Return True when the video row should be marked 'skipped'."""
    return (readiness_word or "").strip() in SKIP_STATES
