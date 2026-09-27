"""The record of scheduled runs (spec 16.2, migration ``0012_schedule.sql``).

One row per task, per subject, per local day. The scheduler reads it to find
out whether the day's run already happened and writes it when it enqueues one,
so "once per local day" is a fact of the database rather than something a
restart forgets.

A run that could not happen is a row as well, with state ``paused`` and the
plain reason, so a schedule that stopped is something the user can read
(spec 16.2, 16.3). Nothing in this module runs a job or decides anything: a row
is written for a decision the caller already made.
"""

from __future__ import annotations

import sqlite3

from .rows import ScheduledRun
from .store import insert

#: A run that enqueued a job.
ENQUEUED = "enqueued"

#: A run that could not happen. It carries a plain reason.
PAUSED = "paused"

#: The states the table accepts (migration ``0012_schedule.sql``).
STATES = (ENQUEUED, PAUSED)


def record_enqueued_run(
    conn: sqlite3.Connection,
    *,
    task: str,
    subject: str,
    local_date: str,
    time_zone: str,
    job_id: int,
) -> int:
    """Record one scheduled run and the job it enqueued. Return the row id."""
    return insert(
        conn,
        "schedule_runs",
        {
            "task": task,
            "subject": subject,
            "local_date": local_date,
            "time_zone": time_zone,
            "state": ENQUEUED,
            "job_id": job_id,
            "reason": None,
        },
    )


def record_paused_run(
    conn: sqlite3.Connection,
    *,
    task: str,
    subject: str,
    local_date: str,
    time_zone: str,
    reason: str,
) -> int:
    """Record one run that could not happen, with its plain reason."""
    text = " ".join(str(reason).split())
    if not text:
        raise ValueError("A paused run needs the reason it could not happen.")
    return insert(
        conn,
        "schedule_runs",
        {
            "task": task,
            "subject": subject,
            "local_date": local_date,
            "time_zone": time_zone,
            "state": PAUSED,
            "job_id": None,
            "reason": text,
        },
    )


def run_for(
    conn: sqlite3.Connection, task: str, subject: str, local_date: str
) -> ScheduledRun | None:
    """Return the run recorded for one subject on one local day, or None."""
    row = conn.execute(
        "SELECT * FROM schedule_runs WHERE task = ? AND subject = ? AND local_date = ?",
        (task, subject, local_date),
    ).fetchone()
    return None if row is None else ScheduledRun.from_row(row)


def runs_on(conn: sqlite3.Connection, local_date: str) -> list[ScheduledRun]:
    """Return every recorded run of one local day, in the order they were made."""
    rows = conn.execute(
        "SELECT * FROM schedule_runs WHERE local_date = ? ORDER BY id", (local_date,)
    ).fetchall()
    return [ScheduledRun.from_row(row) for row in rows]


def latest_runs(conn: sqlite3.Connection, limit: int = 20) -> list[ScheduledRun]:
    """Return the most recent recorded runs, newest first."""
    rows = conn.execute(
        "SELECT * FROM schedule_runs ORDER BY local_date DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [ScheduledRun.from_row(row) for row in rows]


def paused_runs(conn: sqlite3.Connection, limit: int = 20) -> list[ScheduledRun]:
    """Return the most recent runs that could not happen, newest first."""
    rows = conn.execute(
        "SELECT * FROM schedule_runs WHERE state = ? ORDER BY local_date DESC, id DESC LIMIT ?",
        (PAUSED, limit),
    ).fetchall()
    return [ScheduledRun.from_row(row) for row in rows]
