"""The rows the health page reads (spec 16.3).

Spec 16.3 forbids a health page that answers "OK" from a status code: it has to
say what the service is actually doing, and that means reading the tables. This
module is those reads and nothing else -- counts of jobs by lane and state, the
kinds the queue holds, the sources with their failures, the newest capture of
each body, and the runs the daily schedule made. Interpreting them, and saying
which of them is a problem, belongs to :mod:`townrecord.status`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

#: A capture state that means "this video was looked at and no captions came
#: out of it" (migration 0005). It is separate from ``pending``, which means
#: nobody has tried yet, and the difference is what a user needs to see.
FAILED_CAPTURE_STATES = ("failed", "skipped")


@dataclass(frozen=True)
class JobStateCount:
    """How many jobs are in one state on one lane."""

    lane: str
    state: str
    count: int


@dataclass(frozen=True)
class BodyCapture:
    """What has been captured for one body, and how the newest capture went."""

    body_id: int
    body: str
    videos: int
    pending: int
    failed: int
    last_video: str | None
    last_state: str | None
    last_at: str | None
    #: The captions of the newest capture, as the two flags spec 8.7 decides on,
    #: read off the transcript version in hand. They are flags and not the state
    #: word because the words are the capture rules'
    #: (:func:`townrecord.capture.settle.state_of`) and this module reads no
    #: capture code; a caller that shows the word translates them. None means the
    #: newest capture has no transcript at all, which is not a state either.
    last_is_provisional: bool | None = None
    last_settled_under_churn: bool | None = None


def job_state_counts(conn: sqlite3.Connection) -> list[JobStateCount]:
    """Return one row per lane and state that holds at least one job."""
    rows = conn.execute(
        "SELECT lane, state, COUNT(*) AS count FROM jobs GROUP BY lane, state ORDER BY lane, state"
    ).fetchall()
    return [JobStateCount(lane=row["lane"], state=row["state"], count=row["count"]) for row in rows]


def job_kinds(conn: sqlite3.Connection) -> list[str]:
    """Return every job kind the queue holds, in name order.

    A kind here that no registered handler serves is a job nothing can run, and
    that is exactly what a health page is for (spec 16.1, 16.3).
    """
    rows = conn.execute("SELECT DISTINCT kind FROM jobs ORDER BY kind").fetchall()
    return [row["kind"] for row in rows]


def oldest_queued_at(conn: sqlite3.Connection) -> str | None:
    """Return when the oldest queued job was created, or None when none is."""
    row = conn.execute(
        "SELECT MIN(created_at) AS oldest FROM jobs WHERE state = 'queued'"
    ).fetchone()
    return None if row is None else row["oldest"]


def running_before(conn: sqlite3.Connection, moment: str) -> int:
    """Return how many running jobs last beat before `moment`.

    A running job past the heartbeat timeout is a worker that stopped
    answering; the runner puts it back in the queue, and until it does the
    count is worth showing.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM jobs WHERE state = 'running' AND heartbeat_at < ?",
        (moment,),
    ).fetchone()
    return 0 if row is None else int(row["count"])


def captures_by_body(conn: sqlite3.Connection) -> list[BodyCapture]:
    """Return one row per body: its captures, and how the newest one went.

    The newest capture is read in the same query as the counts, so the state
    shown for a body is the state of the video whose timestamp is shown with
    it, and never a mixture of two videos. The captions of that same video come
    from its own version in hand -- the same pick the API makes (spec 8.7:
    ``ORDER BY is_provisional, id DESC``), so the two never disagree about which
    version a body's captions are.
    """
    rows = conn.execute(
        """
        WITH captures AS (
            SELECT m.body_id AS body_id,
                   v.platform_video_id AS platform_video_id,
                   v.capture_state AS capture_state,
                   t.is_provisional AS is_provisional,
                   t.settled_under_churn AS settled_under_churn,
                   COALESCE(v.published_at, v.created_at) AS captured_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.body_id
                       ORDER BY COALESCE(v.published_at, v.created_at) DESC, v.id DESC
                   ) AS recency
            FROM videos v
            JOIN meetings m ON m.id = v.meeting_id
            LEFT JOIN transcripts t ON t.id = (
                SELECT id FROM transcripts
                WHERE video_id = v.id
                ORDER BY is_provisional, id DESC
                LIMIT 1
            )
        )
        SELECT b.id AS body_id,
               b.name AS body,
               COUNT(c.body_id) AS videos,
               COALESCE(SUM(CASE WHEN c.capture_state = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
               COALESCE(SUM(CASE WHEN c.capture_state IN ('failed', 'skipped')
                                 THEN 1 ELSE 0 END), 0) AS failed,
               MAX(CASE WHEN c.recency = 1 THEN c.platform_video_id END) AS last_video,
               MAX(CASE WHEN c.recency = 1 THEN c.capture_state END) AS last_state,
               MAX(CASE WHEN c.recency = 1 THEN c.captured_at END) AS last_at,
               MAX(CASE WHEN c.recency = 1 THEN c.is_provisional END) AS last_is_provisional,
               MAX(CASE WHEN c.recency = 1 THEN c.settled_under_churn END)
                   AS last_settled_under_churn
        FROM bodies b
        LEFT JOIN captures c ON c.body_id = b.id
        GROUP BY b.id, b.name
        ORDER BY b.name, b.id
        """
    ).fetchall()
    return [
        BodyCapture(
            body_id=row["body_id"],
            body=row["body"],
            videos=int(row["videos"]),
            pending=int(row["pending"]),
            failed=int(row["failed"]),
            last_video=row["last_video"],
            last_state=row["last_state"],
            last_at=row["last_at"],
            last_is_provisional=(
                None if row["last_is_provisional"] is None else bool(row["last_is_provisional"])
            ),
            last_settled_under_churn=(
                None
                if row["last_settled_under_churn"] is None
                else bool(row["last_settled_under_churn"])
            ),
        )
        for row in rows
    ]


__all__ = [
    "FAILED_CAPTURE_STATES",
    "BodyCapture",
    "JobStateCount",
    "captures_by_body",
    "job_kinds",
    "job_state_counts",
    "oldest_queued_at",
    "running_before",
]
