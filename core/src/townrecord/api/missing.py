"""The missing-records catalog, per body (spec 9.5).

The per-meeting sentences in ``read.py`` say what one meeting is missing, in
plain words, and are unchanged by this module. This is the other shape of the
same fact: a catalog of what every body is missing *right now*, with how long
each gap has been open, so a person can see that a body has been silent for a
week without opening a week of meetings.

Both routes reconcile the stored findings before they answer, through
``repo.missing.sync``. The list and both counts are derived from the same
function, so the count here can never disagree with the catalog below it; what
the sync adds is the durable row: the moment the gap opened and whether it has
since been filled. That is the one write behind these two reads, and it writes
only rows that are true of the meetings as they stand.

Every route needs the bearer token, like every other one (spec 13.1, decision
10 rule 2): it is the application-level check in ``app.py``, not a dependency
repeated here.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from ..jobs import utcnow
from ..repo import catalogs, entries, filled_count, sync
from ..repo.missing import COUNCIL_KINDS, body_of
from .auth import get_connection
from .models import BodiesOut, BodyMissingSummary, MissingCatalogOut, MissingRecordOut
from .read import not_found

router = APIRouter(prefix="/v1", tags=["missing"])

#: The kinds in the words of a reader, for the note that says why a body is
#: not on the catalog. The four are the ones spec 9.5 reads.
_KIND_WORDS = {
    "council": "a council",
    "commission": "a commission",
    "board": "a board",
    "authority": "an authority",
}

#: The words for the four kinds, as one list a sentence can read.
COUNCIL_WORDS = ", ".join(_KIND_WORDS[kind] for kind in COUNCIL_KINDS)


def _flagged(body_type: str | None) -> bool:
    """Whether the missing-record rule reads a body of this kind."""
    return body_type in COUNCIL_KINDS


def _why_not_flagged(body_type: str | None) -> str:
    """Say in words why the rule does not read this body (decision 10 rule F)."""
    if body_type is None:
        return (
            "Nobody has stated what kind of body this is, so the missing-record "
            f"rule does not read it. It reads {COUNCIL_WORDS}. A body with no "
            "stated kind is not read as one of them."
        )
    return (
        f"This body is stated to be {body_type!r}, which is not one of the kinds "
        f"the missing-record rule reads ({COUNCIL_WORDS})."
    )


def _record_out(entry) -> MissingRecordOut:
    """Publish one open gap of one body."""
    return MissingRecordOut(
        meeting_id=entry.meeting_id,
        meeting_title=entry.meeting_title,
        kind=entry.kind,
        starts_at=entry.starts_at,
        since=entry.since,
        missing_hours=entry.hours,
        missing_days=entry.days,
        alert_days=entry.alert_days,
        alert_due=entry.alert_due,
    )


def _alert_note(body_id: int, days: int, due: int) -> str:
    """Say that a threshold has been crossed and that nothing is sent."""
    return (
        f"{due} of this body's records have been missing longer than the {days} "
        "days it set. The catalog reports the threshold; no alert is delivered "
        "by this build (spec 12.7 is a later unit)."
    )


@router.get(
    "/bodies",
    response_model=BodiesOut,
    summary="Every body, and how many of them have an open gap",
)
def get_bodies(conn: sqlite3.Connection = Depends(get_connection)) -> BodiesOut:
    """Return every body with how many records it is missing right now.

    A body missing nothing is still here, with a zero: a list that dropped the
    bodies with nothing missing could not be told apart from a list nobody
    computed. ``count`` is the number of bodies carrying at least one open gap,
    which is the one-line figure ``townrecord status`` prints, and it is read
    from the same list the per-body catalog is read from, so the two agree by
    construction rather than by a second query that might drift.
    """
    now = utcnow()
    sync(conn, now=now)
    found = catalogs(conn, now=now)
    rows = conn.execute(
        "SELECT id, jurisdiction_id, name, type, missing_alert_days FROM bodies ORDER BY name, id"
    ).fetchall()
    bodies: list[BodyMissingSummary] = []
    for row in rows:
        body_id = int(row["id"])
        missing = found.get(body_id, [])
        bodies.append(
            BodyMissingSummary(
                id=body_id,
                jurisdiction_id=int(row["jurisdiction_id"]),
                name=str(row["name"]),
                type=row["type"],
                missing_count=len(missing),
                oldest_days=max((entry.days for entry in missing), default=None),
                alert_days=row["missing_alert_days"],
                alert_due=any(entry.alert_due for entry in missing),
            )
        )
    return BodiesOut(count=sum(1 for body in bodies if body.missing_count), bodies=bodies)


@router.get(
    "/bodies/{body_id}/missing",
    response_model=MissingCatalogOut,
    summary="What one body is missing right now",
)
def get_body_missing(
    body_id: int, conn: sqlite3.Connection = Depends(get_connection)
) -> MissingCatalogOut:
    """Return the catalog of one body: what is missing, and since when.

    The rule is the one of spec 9.5, read from the rows that hold its four
    facts, so a record that appeared since the last reading is off this list at
    once. ``filled_count`` is how many of this body's gaps have since been
    filled: those rows are kept rather than deleted, which is what lets the
    catalog say a record arrived.
    """
    body = body_of(conn, body_id)
    if body is None:
        raise not_found(f"There is no body {body_id}.")
    now = utcnow()
    sync(conn, now=now)
    missing = entries(conn, now=now, body_id=body_id)
    notes: list[str] = []
    if not _flagged(body.type):
        notes.append(_why_not_flagged(body.type))
    if body.missing_alert_days is not None:
        due = sum(1 for entry in missing if entry.alert_due)
        if due:
            notes.append(_alert_note(body_id, int(body.missing_alert_days), due))
    return MissingCatalogOut(
        body_id=body.id,
        body_name=body.name,
        body_type=body.type,
        jurisdiction_id=body.jurisdiction_id,
        flagged=_flagged(body.type),
        alert_days=body.missing_alert_days,
        count=len(missing),
        filled_count=filled_count(conn, body_id=body_id),
        missing=[_record_out(entry) for entry in missing],
        notes=notes,
    )
