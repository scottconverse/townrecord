"""The rows spec 8.7 writes: caption checks, review items and rerun marks.

Spec 8.7 asks for four things to be remembered. A recheck happened and what it
saw (``caption_checks``), a revision was found (a new row of ``transcripts``,
which :mod:`townrecord.repo.text` writes), the user is owed one item about it
(``review_items``), and the work that used the older version is stale
(``rerun_marks``). This module is those three tables and the settling of a
transcript, as SQL, and nothing about when any of it should happen: the rules
are in :mod:`townrecord.capture.settle`, which reads no database at all.

Nothing here rewrites a row. A revision is new bytes, so it is a new artifact
and a new transcript row, and every older version stays readable with its own
hash (spec 8.6, 8.7). The one update in this module is the one the spec asks
for: a provisional transcript that has stopped changing is settled.
"""

from __future__ import annotations

import sqlite3

from .rows import CaptionCheck, ProvisionalCapture, RerunMark, ReviewItem
from .store import insert

#: The kinds of work a revision puts back in doubt. Each names a table, and
#: the CHECK on ``rerun_marks.kind`` holds the same three names.
RERUN_ALIGNMENT = "alignment"
RERUN_MOTION = "motion"
RERUN_SPEAKERS = "speakers"

#: The kind of review item a caption revision raises today.
REVISION_ITEM = "caption_revision"


def insert_check(
    conn: sqlite3.Connection,
    *,
    video_id: int,
    transcript_id: int,
    checked_at: str,
    changed: bool = False,
    what_changed: str | None = None,
    observed_sha256: str | None = None,
    observed_revision_at: str | None = None,
    observed_duration_s: float | None = None,
    note: str = "",
) -> int:
    """Store one recheck of one video and return its id (spec 8.7).

    ``checked_at`` is the moment of the check rather than the moment of the
    write, because it is what the 1 hour rule is measured between. A check that
    changed nothing carries ``what_changed`` None, which the schema enforces in
    both directions: a change names a signal, and no change names none.
    """
    return insert(
        conn,
        "caption_checks",
        {
            "video_id": video_id,
            "transcript_id": transcript_id,
            "checked_at": checked_at,
            "changed": int(changed),
            "what_changed": what_changed,
            "observed_sha256": observed_sha256,
            "observed_revision_at": observed_revision_at,
            "observed_duration_s": observed_duration_s,
            "note": note,
        },
    )


def checks_of(conn: sqlite3.Connection, video_id: int) -> list[CaptionCheck]:
    """Return every check of one video, oldest first.

    Oldest first because that is the order the rules read them in: the 1 hour
    rule is about the last two, and "still changing" is about all of them.
    """
    rows = conn.execute(
        "SELECT * FROM caption_checks WHERE video_id = ? ORDER BY checked_at, id", (video_id,)
    ).fetchall()
    return [CaptionCheck.from_row(row) for row in rows]


def latest_check(conn: sqlite3.Connection, video_id: int) -> CaptionCheck | None:
    """Return the most recent check of one video, or None when it has none."""
    row = conn.execute(
        "SELECT * FROM caption_checks WHERE video_id = ? ORDER BY checked_at DESC, id DESC LIMIT 1",
        (video_id,),
    ).fetchone()
    return None if row is None else CaptionCheck.from_row(row)


def first_capture_at(conn: sqlite3.Connection, video_id: int) -> str | None:
    """Return when this video's first transcript was stored, or None.

    The 24 hour window and the 48 hour backstop are both counted from the first
    capture rather than from the newest version: a revision does not restart the
    clock, or a captioner that kept editing would never be settled at all.
    """
    row = conn.execute(
        "SELECT MIN(created_at) AS first FROM transcripts WHERE video_id = ?", (video_id,)
    ).fetchone()
    return None if row is None else row["first"]


def settle_transcripts(
    conn: sqlite3.Connection, *, video_id: int, settled_at: str, under_churn: bool = False
) -> int:
    """Mark every provisional transcript of one video settled. Return how many.

    Every version and not only the newest: the older ones stopped changing when
    the revision replaced them, and a row left provisional would keep being
    preferred to the version in hand by ``latest_transcript``. A capture whose
    transcript was still changing at the 48 hour backstop settles with
    ``under_churn``, which is the state the spec calls "settled under churn".
    """
    cursor = conn.execute(
        "UPDATE transcripts SET is_provisional = 0, settled_under_churn = ?, settled_at = ? "
        "WHERE video_id = ? AND is_provisional = 1",
        (int(under_churn), settled_at, video_id),
    )
    return int(cursor.rowcount if cursor.rowcount is not None and cursor.rowcount > 0 else 0)


def raise_review_item(
    conn: sqlite3.Connection,
    *,
    kind: str,
    subject: str,
    sentence: str,
    meeting_id: int | None = None,
    video_id: int | None = None,
) -> int | None:
    """Raise one review item, or return None when it was already raised.

    Returns None rather than raising on a repeat because the repeat is the
    normal case, not a mistake: ``subject`` is the identity of the thing under
    review, so a second ask about the same revision is the same item. That is
    what makes the spec's "raises one review item" true however many times the
    pass that found it runs (spec 8.7, 16.1).
    """
    try:
        return insert(
            conn,
            "review_items",
            {
                "kind": kind,
                "subject": subject,
                "meeting_id": meeting_id,
                "video_id": video_id,
                "sentence": sentence,
            },
        )
    except sqlite3.IntegrityError:
        return None


def review_items(
    conn: sqlite3.Connection, *, video_id: int | None = None, kind: str | None = None
) -> list[ReviewItem]:
    """Return the review items the user has not answered, newest first.

    Filtered by video, by kind, or by neither, which is how a meeting's page and
    a whole-service page each ask their own question.
    """
    clauses: list[str] = []
    values: list[object] = []
    if video_id is not None:
        clauses.append("video_id = ?")
        values.append(video_id)
    if kind is not None:
        clauses.append("kind = ?")
        values.append(kind)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM review_items{where} ORDER BY created_at DESC, id DESC", tuple(values)
    ).fetchall()
    return [ReviewItem.from_row(row) for row in rows]


def mark_for_rerun(
    conn: sqlite3.Connection,
    *,
    kind: str,
    row_id: int,
    meeting_id: int,
    reason: str,
) -> int | None:
    """Mark one row of work stale, or return None when it is marked already.

    The row is never changed: spec 8.7 says a revision does not rewrite what was
    already exported or published, so the mark is a new row that says the work
    was made from a version that has been replaced. Marking the same row for the
    same reason twice writes one mark.
    """
    if kind not in (RERUN_ALIGNMENT, RERUN_MOTION, RERUN_SPEAKERS):
        known = ", ".join((RERUN_ALIGNMENT, RERUN_MOTION, RERUN_SPEAKERS))
        raise ValueError(f"A rerun mark is one of {known}, not {kind!r}.")
    try:
        return insert(
            conn,
            "rerun_marks",
            {"kind": kind, "row_id": row_id, "meeting_id": meeting_id, "reason": reason},
        )
    except sqlite3.IntegrityError:
        return None


def rerun_marks_of(conn: sqlite3.Connection, meeting_id: int) -> list[RerunMark]:
    """Return the stale work of one meeting, oldest first."""
    rows = conn.execute(
        "SELECT * FROM rerun_marks WHERE meeting_id = ? ORDER BY created_at, id", (meeting_id,)
    ).fetchall()
    return [RerunMark.from_row(row) for row in rows]


def provisional_captures(
    conn: sqlite3.Connection, *, limit: int | None = None
) -> list[ProvisionalCapture]:
    """Return the videos whose captions may still change, oldest video first.

    One row per video, read off the version in hand -- the same row
    :func:`townrecord.repo.text.latest_transcript` returns -- so the state word a
    caller shows is the state of the transcript the rest of the system is reading
    and not of one the revision replaced.

    The newest check is carried alongside because it is the other half of what a
    user asks about a provisional capture: when it was last looked at. The moment
    of the next check is not here: it belongs to the queue, and the queue is not
    this table (spec 16.1).
    """
    sql = (
        "SELECT v.id AS video_id, v.platform_video_id AS platform_video_id, v.title AS title, "
        "v.meeting_id AS meeting_id, t.id AS transcript_id, "
        "t.is_provisional AS is_provisional, t.settled_under_churn AS settled_under_churn, "
        "(SELECT MAX(c.checked_at) FROM caption_checks c WHERE c.video_id = v.id) AS checked_at "
        "FROM videos v JOIN transcripts t ON t.id = ("
        "SELECT id FROM transcripts WHERE video_id = v.id "
        "ORDER BY is_provisional, id DESC LIMIT 1) "
        "WHERE t.is_provisional = 1 ORDER BY v.id"
    )
    if limit is not None:
        sql += " LIMIT ?"
        rows = conn.execute(sql, (limit,)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()
    return [ProvisionalCapture.from_row(row) for row in rows]


__all__ = [
    "RERUN_ALIGNMENT",
    "RERUN_MOTION",
    "RERUN_SPEAKERS",
    "REVISION_ITEM",
    "checks_of",
    "first_capture_at",
    "insert_check",
    "latest_check",
    "mark_for_rerun",
    "provisional_captures",
    "raise_review_item",
    "rerun_marks_of",
    "review_items",
    "settle_transcripts",
]
