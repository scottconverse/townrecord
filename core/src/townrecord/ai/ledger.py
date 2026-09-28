"""The usage ledger and the estimate that comes before it (spec 11.8).

Two halves of the same promise.

The **ledger** counts calls per provider, per day and per month, in one row per
provider, task and local day (``ai_usage``, migration 0016). The month is not a
second row: it is the first seven characters of the day, so a month total is a
sum over the days that exist and no two numbers can disagree.

The **estimate** answers the other half of spec 11.8: show the expected model
calls for a big job before it starts, for example a first capture of 30 days.
It counts calls from the work the job is about to do, so a user who is about to
spend an afternoon of their own machine's time sees the number first.

The days here are the days the area's clock reads, not UTC days. Spec 16.2
measures a day in the area's time zone, and a ledger that counted UTC days
would move a council meeting that ended at 22:00 in Denver into tomorrow.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date

from .ladder import (
    TASK_ALIGN,
    TASK_ANSWER,
    TASK_CLASSIFY,
    TASK_DISCOVER,
    TASK_EMBED,
    TASK_FACT_CHECK,
    TASK_OCR,
    TASK_SUMMARIZE,
)

#: How many calls each task makes for one unit of work, and which unit that is.
#: Most of these are one call per thing, which is what spec 11.4's task table
#: describes: a classify call decides one video, an align call helps with one
#: meeting, an OCR call reads one page.
#:
#: The summarize figure is the spec's own measurement and not a guess: spec
#: 11.6 records that a four-hour council meeting draft "takes about 20 minutes
#: across nine local-model calls".
CALLS_PER_UNIT: Mapping[str, tuple[str, int]] = {
    TASK_CLASSIFY: ("videos", 1),
    TASK_DISCOVER: ("sources", 1),
    TASK_ALIGN: ("meetings", 1),
    TASK_SUMMARIZE: ("meetings", 9),
    TASK_OCR: ("pages", 1),
    TASK_EMBED: ("items", 1),
    TASK_ANSWER: ("questions", 1),
    TASK_FACT_CHECK: ("questions", 1),
}


def _day_text(day: str | date) -> str:
    """The stored spelling of one local day, as YYYY-MM-DD."""
    return day.isoformat() if isinstance(day, date) else str(day).strip()


def _month_of(day: str) -> str:
    """The month a stored day belongs to, as YYYY-MM."""
    return day[:7]


def record_call(
    conn: sqlite3.Connection,
    *,
    provider: str,
    task: str,
    day: str | date,
    calls: int = 1,
) -> int:
    """Add calls to one provider's row for one task and day. Return the total."""
    if calls <= 0:
        raise ValueError(f"A ledger entry is a positive number of calls, not {calls}.")
    text = _day_text(day)
    conn.execute(
        """
        INSERT INTO ai_usage (provider, task, day, calls) VALUES (?, ?, ?, ?)
        ON CONFLICT (provider, task, day) DO UPDATE SET calls = calls + excluded.calls
        """,
        (provider, task, text, int(calls)),
    )
    return calls_on(conn, provider=provider, task=task, day=text)


def calls_on(conn: sqlite3.Connection, *, provider: str, task: str = "", day: str | date) -> int:
    """How many calls one provider made on one day, for one task or for all."""
    text = _day_text(day)
    if task:
        row = conn.execute(
            "SELECT COALESCE(SUM(calls), 0) AS calls FROM ai_usage "
            "WHERE provider = ? AND task = ? AND day = ?",
            (provider, task, text),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(calls), 0) AS calls FROM ai_usage WHERE provider = ? AND day = ?",
            (provider, text),
        ).fetchone()
    return int(row["calls"])


def day_totals(conn: sqlite3.Connection, day: str | date) -> dict[str, int]:
    """Every provider's calls on one day, by provider name."""
    text = _day_text(day)
    rows = conn.execute(
        "SELECT provider, SUM(calls) AS calls FROM ai_usage WHERE day = ? "
        "GROUP BY provider ORDER BY provider",
        (text,),
    ).fetchall()
    return {str(row["provider"]): int(row["calls"]) for row in rows}


def month_totals(conn: sqlite3.Connection, month: str | date) -> dict[str, int]:
    """Every provider's calls in one month, by provider name (spec 11.8).

    The month is read from the days already stored, so it can never disagree
    with them. Callers pass ``YYYY-MM``, or a date and the month it falls in.
    """
    text = month.strftime("%Y-%m") if isinstance(month, date) else _month_of(_day_text(month))
    rows = conn.execute(
        "SELECT provider, SUM(calls) AS calls FROM ai_usage WHERE substr(day, 1, 7) = ? "
        "GROUP BY provider ORDER BY provider",
        (text,),
    ).fetchall()
    return {str(row["provider"]): int(row["calls"]) for row in rows}


def total_for(
    conn: sqlite3.Connection,
    provider: str,
    *,
    day: str | date | None = None,
    month: str | date | None = None,
) -> int:
    """One provider's calls on a day, in a month, or over everything stored."""
    if day is not None:
        return calls_on(conn, provider=provider, day=day)
    if month is not None:
        return month_totals(conn, month).get(provider, 0)
    row = conn.execute(
        "SELECT COALESCE(SUM(calls), 0) AS calls FROM ai_usage WHERE provider = ?",
        (provider,),
    ).fetchone()
    return int(row["calls"])


@dataclass(frozen=True)
class PlannedWork:
    """What a job is about to do, in the units the estimate counts."""

    #: How many days the job covers, so the estimate can say "a first capture
    #: of 30 days" rather than "this job" (spec 11.8).
    days: int = 0
    videos: int = 0
    sources: int = 0
    meetings: int = 0
    pages: int = 0
    items: int = 0
    questions: int = 0

    def units(self, name: str) -> int:
        """How many of one unit this work holds."""
        return max(0, int(getattr(self, name, 0)))


@dataclass(frozen=True)
class Estimate:
    """The model calls a planned job is expected to make (spec 11.8)."""

    work: PlannedWork
    calls: Mapping[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        """Every call the job is expected to make."""
        return sum(self.calls.values())

    def sentence(self) -> str:
        """One plain sentence a user can read before the job starts."""
        if self.work.days:
            opening = f"A first capture of {self.work.days} days is about"
        else:
            opening = "This job is about"
        if not self.total:
            return f"{opening} no model calls."
        parts = ", ".join(f"{calls} {task}" for task, calls in sorted(self.calls.items()))
        return f"{opening} {self.total} model calls: {parts}."


def estimate_calls(work: PlannedWork) -> Estimate:
    """Count the model calls a job is about to make (spec 11.8).

    A task with no work of its unit makes no calls, so the answer names only
    the tasks this job will actually run.
    """
    calls: dict[str, int] = {}
    for task, (unit, per_unit) in CALLS_PER_UNIT.items():
        count = work.units(unit) * per_unit
        if count:
            calls[task] = count
    return Estimate(work=work, calls=calls)
