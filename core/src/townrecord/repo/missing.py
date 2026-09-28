"""The records a body should have and does not (spec 9.5).

Spec 9.5 puts the rule in the words of TownReporter's own finding: flag missing
minutes when there is no minutes document, 36 hours or more have passed, the
body is a council, a commission, a board or an authority, and the meeting was
not cancelled or continued. That is "a catalog note, not a story". This module
is that note, per body, with how long each gap has been open.

The rule reads four facts, and each one is read from the row that holds it:

* **no minutes document.** Neither a ``records`` row of kind ``'minutes'`` for
  the meeting, nor a ``minutes_documents`` row: the draft of one session's
  minutes is read out of the next regular session's packet (spec 9.4), and
  either one is the record existing. A meeting whose minutes were only ever
  found inside another meeting's packet is not missing them.
* **36 hours or more.** Counted from ``meetings.starts_at``
  (:data:`MISSING_AFTER`). The boundary is inclusive, exactly as the spec
  writes it: a meeting 36 hours old has a gap.
* **the body's own kind.** ``bodies.type`` is one of :data:`COUNCIL_KINDS`. A
  body whose kind nobody has stated is not one of the four, so it is never
  flagged, and the catalog says so in words rather than staying quiet
  (rule F). ``'other'`` is a stated kind meaning none of the four.
* **not cancelled and not continued.** The meeting's own two flags.

An elapsed time and a local time are two different questions. ``starts_at`` is
the local time the body published, ISO 8601, with its offset when the source
gave one (spec 16.2), and the offset is what "36 hours from then" is measured
in: a meeting published at 19:00 in Denver is 36 hours old at 07:00 UTC two
days later. Nothing is rewritten to UTC and no stored string is ever changed,
which is why :func:`moment_of` reads the offset rather than normalizing it
away. A timestamp with no offset is read as UTC, the reading the job queue's
own stamps use.

**The catalog is derived**, on every read, from those four facts, so a record
that appears afterwards is off the list at once and a list is never stale.
What is *stored*, in ``missing_records``, is the record of the finding: when
the gap opened, and whether it has since been filled. :func:`sync` brings that
record up to date, and the two read routes of the API call it before they
answer, so a reader of the database sees the same set the API answers with.
Nothing in this module deletes a row.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .rows import Body, MissingRecord

#: The kinds of body the rule reads (spec 9.5). A body whose kind is NULL, or
#: whose kind is ``'other'``, is not one of these and is never flagged.
COUNCIL_KINDS: tuple[str, ...] = ("council", "commission", "board", "authority")

#: Every kind of body a person can state (migration 0018). ``'other'`` exists
#: so that "none of the four" can be said, which is a different fact from
#: nobody having stated a kind.
BODY_KINDS: tuple[str, ...] = COUNCIL_KINDS + ("other",)

#: How long may pass before a record that is absent is a finding. Spec 9.5
#: writes "36 hours or more", so the comparison is inclusive: a meeting
#: exactly 36 hours old has a gap.
MISSING_AFTER = timedelta(hours=36)

#: The one record kind the rule is stated for. Spec 9.5 states it for minutes
#: alone, so only a minutes gap is opened today; the migration accepts the
#: agenda and the packet so a later rule of the spec's own can open one
#: without a schema change (decision 10 rule C).
MINUTES = "minutes"
RULE_KINDS: tuple[str, ...] = (MINUTES,)

#: The two states a stored finding is in. ``open`` is a record still missing,
#: ``filled`` is one that appeared.
OPEN = "open"
FILLED = "filled"

#: The kinds of record a stored finding can name, which the migration's own
#: check repeats. Nothing here opens an agenda or a packet gap yet.
CATALOG_KINDS: tuple[str, ...] = ("agenda", "packet", "minutes")


@dataclass(frozen=True)
class Gap:
    """One record that should exist and does not, as the rule reads it now.

    ``opens_at`` is the moment the gap opened: the meeting's start plus the
    hours the rule waits. It is derived, so the same gap carries the same
    moment on every read and however late the row was written.
    """

    meeting_id: int
    body_id: int
    kind: str
    starts_at: str
    opens_at: str


@dataclass(frozen=True)
class Entry:
    """One gap of one body, with the meeting it belongs to and its age.

    ``hours`` and ``days`` are whole numbers, rounded down, so a caller never
    reads a fraction it did not ask for. ``alert_days`` is the body's own
    setting (spec 12.7) and ``alert_due`` says whether the threshold has been
    crossed. No alert is delivered by anything in this unit.
    """

    meeting_id: int
    body_id: int
    kind: str
    meeting_title: str | None
    starts_at: str
    since: str
    hours: int
    days: int
    alert_days: int | None
    alert_due: bool


@dataclass(frozen=True)
class Finding:
    """What one :func:`sync` did to the stored record of the findings."""

    opened: int
    filled: int
    reopened: int


def moment_of(starts_at: str) -> datetime:
    """Read a published local time as an instant, honoring the offset it carries.

    A timestamp with no offset is read as UTC, which is the reading the job
    queue's own stamps use, so a caller cannot silently introduce a local time.
    The string is never rewritten: an elapsed time is measured against the
    offset the publisher gave, and no stored value changes.
    """
    moment = datetime.fromisoformat(starts_at)
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def _at(moment: datetime) -> datetime:
    """Read a moment as UTC, reading a naive one as UTC rather than as local."""
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def _text(moment: datetime) -> str:
    """Return a UTC timestamp string that also sorts in time order.

    The format is the one the database writes its own ``created_at`` columns
    with, and the one ``townrecord.jobs.queue.stamp`` writes. It is spelled
    here rather than imported because the repository layer reads the job tables
    through SQL and never imports the job queue.
    """
    return _at(moment).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def record_present(conn: sqlite3.Connection, meeting_id: int, kind: str) -> bool:
    """Whether the record of that kind exists for that meeting now.

    Minutes are the one kind the spec reads in two places: a minutes document
    of the meeting's own, or the draft of them read out of a later meeting's
    packet (spec 9.4, a ``minutes_documents`` row). Either one is the record
    existing, so either one closes the gap.
    """
    row = conn.execute(
        "SELECT 1 FROM records WHERE meeting_id = ? AND kind = ? LIMIT 1",
        (meeting_id, kind),
    ).fetchone()
    if row is not None:
        return True
    if kind != MINUTES:
        return False
    return (
        conn.execute(
            "SELECT 1 FROM minutes_documents WHERE meeting_id = ? LIMIT 1", (meeting_id,)
        ).fetchone()
        is not None
    )


def gaps(conn: sqlite3.Connection, *, now: datetime) -> list[Gap]:
    """Return every record that should exist now and does not, per meeting.

    The meetings are read by the four facts of the rule -- an eligible body, no
    cancellation, no continuation -- and the age of each is measured in Python,
    because a published timestamp carries an offset and comparing one as text
    would compare two different things. Oldest gap first.
    """
    moment = _at(now)
    placeholders = ", ".join("?" for _ in COUNCIL_KINDS)
    rows = conn.execute(
        "SELECT meetings.id AS meeting_id, meetings.body_id AS body_id, "
        "meetings.starts_at AS starts_at "
        "FROM meetings JOIN bodies ON bodies.id = meetings.body_id "
        f"WHERE bodies.type IN ({placeholders}) "
        "AND meetings.is_cancelled = 0 AND meetings.is_continued = 0 "
        "ORDER BY meetings.starts_at, meetings.id",
        COUNCIL_KINDS,
    ).fetchall()
    found: list[Gap] = []
    for row in rows:
        started = moment_of(str(row["starts_at"]))
        opened = started + MISSING_AFTER
        if moment < opened:
            continue
        for kind in RULE_KINDS:
            if record_present(conn, int(row["meeting_id"]), kind):
                continue
            found.append(
                Gap(
                    meeting_id=int(row["meeting_id"]),
                    body_id=int(row["body_id"]),
                    kind=kind,
                    starts_at=str(row["starts_at"]),
                    opens_at=_text(opened),
                )
            )
    found.sort(key=lambda gap: (gap.opens_at, gap.meeting_id, gap.kind))
    return found


def sync(conn: sqlite3.Connection, *, now: datetime) -> Finding:
    """Bring the stored findings in step with the rule, and say what changed.

    A gap with no row gets one, open. A stored row whose gap is filled gets
    ``state = 'filled'`` and the moment, and is kept: nothing is deleted, so
    the catalog can say that a record arrived. A row marked filled whose gap is
    back is open again, on its original ``opened_at``, because that is when the
    gap was first seen.

    Every value written is derived from the meetings, so a sync that runs late
    writes the same row a timely one would have written.
    """
    moment = _at(now)
    wanted = {(gap.meeting_id, gap.kind): gap for gap in gaps(conn, now=moment)}
    stored = conn.execute("SELECT * FROM missing_records").fetchall()
    opened = filled = reopened = 0
    for row in stored:
        found = MissingRecord.from_row(row)
        if (found.meeting_id, found.kind) in wanted:
            if found.state == FILLED:
                conn.execute(
                    "UPDATE missing_records SET state = ?, filled_at = NULL WHERE id = ?",
                    (OPEN, found.id),
                )
                reopened += 1
            continue
        if found.state == OPEN:
            conn.execute(
                "UPDATE missing_records SET state = ?, filled_at = ? WHERE id = ?",
                (FILLED, _text(moment), found.id),
            )
            filled += 1
    known = {(int(row["meeting_id"]), str(row["kind"])) for row in stored}
    for key, gap in wanted.items():
        if key in known:
            continue
        conn.execute(
            "INSERT INTO missing_records (meeting_id, kind, opened_at) VALUES (?, ?, ?)",
            (gap.meeting_id, gap.kind, gap.opens_at),
        )
        opened += 1
    return Finding(opened=opened, filled=filled, reopened=reopened)


def catalogs(conn: sqlite3.Connection, *, now: datetime) -> dict[int, list[Entry]]:
    """Return the open gaps of each body that has one, oldest first per body.

    The meeting title comes from the meeting row, the age is measured from the
    moment the gap opened, and the alert setting comes from the body's own row.
    The bodies and the meetings are read in two passes and not one per body, so
    a caller listing every body asks the database the same number of times
    whether the area holds three bodies or thirty. Nothing here writes.
    """
    moment = _at(now)
    alerts = {
        int(row["id"]): row["missing_alert_days"]
        for row in conn.execute("SELECT id, missing_alert_days FROM bodies")
    }
    titles = {
        int(row["id"]): row["title"] for row in conn.execute("SELECT id, title FROM meetings")
    }
    found: dict[int, list[Entry]] = {}
    for gap in gaps(conn, now=moment):
        missing_for = moment - moment_of(gap.opens_at)
        alert_days = alerts.get(gap.body_id)
        threshold = None if alert_days is None else timedelta(days=int(alert_days))
        found.setdefault(gap.body_id, []).append(
            Entry(
                meeting_id=gap.meeting_id,
                body_id=gap.body_id,
                kind=gap.kind,
                meeting_title=titles.get(gap.meeting_id),
                starts_at=gap.starts_at,
                since=gap.opens_at,
                hours=int(missing_for.total_seconds() // 3600),
                days=missing_for.days,
                alert_days=None if alert_days is None else int(alert_days),
                alert_due=threshold is not None and missing_for >= threshold,
            )
        )
    return found


def entries(conn: sqlite3.Connection, *, body_id: int, now: datetime) -> list[Entry]:
    """Return the open gaps of one body, with the age of each."""
    return catalogs(conn, now=now).get(body_id, [])


def counts(conn: sqlite3.Connection, *, now: datetime) -> dict[int, int]:
    """Return how many open gaps each body has, for the bodies that have one."""
    return {body_id: len(found) for body_id, found in catalogs(conn, now=now).items()}


def filled_count(conn: sqlite3.Connection, *, body_id: int) -> int:
    """Return how many of one body's findings have been filled, ever.

    A filled finding is a record that was missing and appeared. The row is kept
    rather than deleted, so this count is the catalog's memory of that.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM missing_records "
        "JOIN meetings ON meetings.id = missing_records.meeting_id "
        "WHERE meetings.body_id = ? AND missing_records.state = ?",
        (body_id, FILLED),
    ).fetchone()
    return int(row["count"])


def set_missing_alert_days(conn: sqlite3.Connection, body_id: int, days: int | None) -> None:
    """Set, change or clear one body's "alert me after N days" (spec 12.7).

    ``None`` clears the setting. Nothing is delivered by this: the catalog row
    says whether the threshold has been crossed, and no notification, list,
    feed or email is built by this unit.
    """
    if days is not None and days < 0:
        raise ValueError("An alert is set for a number of days that is zero or more.")
    before = conn.execute("SELECT 1 FROM bodies WHERE id = ?", (body_id,)).fetchone()
    if before is None:
        raise KeyError(f"There is no body {body_id}.")
    conn.execute("UPDATE bodies SET missing_alert_days = ? WHERE id = ?", (days, body_id))


def set_body_type(conn: sqlite3.Connection, body_id: int, type: str | None) -> None:
    """State what kind of body one body is, or clear the statement (spec 9.5).

    The four kinds the missing-record rule reads are the ones the column
    accepts, plus ``'other'`` for a body that is none of them. The table
    refuses anything else, so a caller cannot write a kind no read knows.
    """
    if type is not None and type not in BODY_KINDS:
        raise ValueError(f"{type!r} is not a kind of body.")
    before = conn.execute("SELECT 1 FROM bodies WHERE id = ?", (body_id,)).fetchone()
    if before is None:
        raise KeyError(f"There is no body {body_id}.")
    conn.execute("UPDATE bodies SET type = ? WHERE id = ?", (type, body_id))


def body_of(conn: sqlite3.Connection, body_id: int) -> Body | None:
    """Return one body, or None when there is no such row."""
    row = conn.execute("SELECT * FROM bodies WHERE id = ?", (body_id,)).fetchone()
    return None if row is None else Body.from_row(row)


__all__ = [
    "BODY_KINDS",
    "CATALOG_KINDS",
    "COUNCIL_KINDS",
    "FILLED",
    "MINUTES",
    "MISSING_AFTER",
    "OPEN",
    "RULE_KINDS",
    "Entry",
    "Finding",
    "Gap",
    "body_of",
    "catalogs",
    "counts",
    "entries",
    "filled_count",
    "gaps",
    "moment_of",
    "record_present",
    "set_body_type",
    "set_missing_alert_days",
    "sync",
]
