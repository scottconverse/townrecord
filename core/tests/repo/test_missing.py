"""The missing-records rule and the catalog it keeps (spec 9.5).

Spec 9.5's rule is four facts, each read from the row that holds it: no minutes
document, 36 hours or more since the meeting, a body of one of the four kinds,
and a meeting that was neither cancelled nor continued. Every test here holds
the clock at one stated moment and reads the rule at that moment, so an age in
an assertion is arithmetic a reader can check and never the wall clock's answer
(PROJECT-BRIEF rule 4).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from townrecord.repo import (
    catalogs,
    counts,
    entries,
    filled_count,
    gaps,
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_minutes_document,
    insert_record,
    moment_of,
    record_present,
    set_missing_alert_days,
    sync,
)
from townrecord.repo.missing import FILLED, MINUTES, MISSING_AFTER, OPEN

from .conftest import PutArtifact

#: The moment every test here reads the rule at. Stated once, so no assertion
#: below depends on when the suite ran.
NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def hours_before(hours: float, *, offset: str = "+00:00") -> str:
    """A meeting start that many hours before NOW, as the publisher wrote it."""
    return (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S") + offset


def only_body(
    conn: sqlite3.Connection, *, type: str | None = "council", town: str = "Longmont"
) -> int:
    """One city and one body of the stated kind.

    ``town`` names the city, because a database holds one city of a name and a
    test that wants two bodies wants them in two towns.
    """
    city = insert_jurisdiction(conn, type="city", name=town)
    return insert_body(conn, jurisdiction_id=city, name="City Council", type=type)


def minutes_record(conn: sqlite3.Connection, put: PutArtifact, meeting_id: int) -> int:
    """A minutes document of one meeting's own, through the real store."""
    artifact = put("minutes", b"%PDF-1.4\n% the minutes as adopted\n", "pdf")
    return insert_record(conn, meeting_id=meeting_id, kind=MINUTES, artifact_id=artifact)


# ------------------------------------------------------------ the four facts --


def test_a_meeting_under_36_hours_old_is_not_flagged(conn: sqlite3.Connection) -> None:
    body = only_body(conn)
    insert_meeting(conn, body_id=body, starts_at=hours_before(35, offset="-06:00"))

    assert gaps(conn, now=NOW) == []
    assert entries(conn, body_id=body, now=NOW) == []
    assert sync(conn, now=NOW).opened == 0


def test_a_meeting_exactly_36_hours_old_is_flagged(conn: sqlite3.Connection) -> None:
    """Spec 9.5 writes "36 hours or more", so the boundary is inclusive."""
    body = only_body(conn)
    meeting = insert_meeting(conn, body_id=body, starts_at=hours_before(36))

    found = gaps(conn, now=NOW)
    assert [gap.meeting_id for gap in found] == [meeting]
    assert [gap.kind for gap in found] == [MINUTES]


def test_a_meeting_one_minute_under_36_hours_is_not_flagged(conn: sqlite3.Connection) -> None:
    """The other side of the same boundary, so the comparison is not merely old."""
    body = only_body(conn)
    started = NOW - MISSING_AFTER + timedelta(minutes=1)
    insert_meeting(conn, body_id=body, starts_at=started.isoformat())

    assert gaps(conn, now=NOW) == []


@pytest.mark.parametrize("flag", ["is_cancelled", "is_continued"])
def test_a_cancelled_or_continued_meeting_is_never_flagged(
    conn: sqlite3.Connection, flag: str
) -> None:
    body = only_body(conn)
    insert_meeting(conn, body_id=body, starts_at=hours_before(48), **{flag: True})

    assert gaps(conn, now=NOW) == []
    assert sync(conn, now=NOW).opened == 0


@pytest.mark.parametrize("type", [None, "other"])
def test_a_body_that_is_not_one_of_the_four_kinds_is_not_flagged(
    conn: sqlite3.Connection, type: str | None
) -> None:
    """A body with no stated kind is not read as a council (rule F)."""
    body = only_body(conn, type=type)
    insert_meeting(conn, body_id=body, starts_at=hours_before(48))

    assert gaps(conn, now=NOW) == []
    assert catalogs(conn, now=NOW) == {}


@pytest.mark.parametrize("type", ["council", "commission", "board", "authority"])
def test_every_kind_of_body_the_rule_reads_is_flagged(conn: sqlite3.Connection, type: str) -> None:
    body = only_body(conn, type=type)
    insert_meeting(conn, body_id=body, starts_at=hours_before(48))

    assert [entry.meeting_id for entry in entries(conn, body_id=body, now=NOW)] == [1]
    assert gaps(conn, now=NOW)[0].body_id == body


def test_a_meeting_with_minutes_of_its_own_is_not_missing_them(
    conn: sqlite3.Connection, put_artifact: PutArtifact
) -> None:
    body = only_body(conn)
    meeting = insert_meeting(conn, body_id=body, starts_at=hours_before(48))
    minutes_record(conn, put_artifact, meeting)

    assert record_present(conn, meeting, MINUTES) is True
    assert gaps(conn, now=NOW) == []


def test_minutes_found_inside_another_meeting_s_packet_are_not_missing(
    conn: sqlite3.Connection, put_artifact: PutArtifact
) -> None:
    """Spec 9.4: one session's minutes are read out of the next session's packet."""
    body = only_body(conn)
    meeting = insert_meeting(conn, body_id=body, starts_at=hours_before(72))
    later = insert_meeting(conn, body_id=body, starts_at=hours_before(24))
    artifact = put_artifact("packet", b"%PDF-1.4\n% the next session's packet\n", "pdf")
    packet = insert_record(conn, meeting_id=later, kind="packet", artifact_id=artifact)
    insert_minutes_document(
        conn,
        meeting_id=meeting,
        record_id=packet,
        start_page=40,
        end_page=52,
        head="CITY COUNCIL MINUTES SEPTEMBER 8, 2026",
    )

    assert gaps(conn, now=NOW) == []


# --------------------------------------------------------- the age, measured --


def test_the_offset_a_meeting_published_is_what_the_age_is_measured_in(
    conn: sqlite3.Connection,
) -> None:
    """Two meetings with the same wall clock and different offsets are not the same age.

    Comparing the two timestamps as text would call them equal, and one of them
    is four hours from the boundary and the other is past it.
    """
    utc = only_body(conn)
    mountain_city = insert_jurisdiction(conn, type="city", name="Boulder")
    mountain = insert_body(conn, jurisdiction_id=mountain_city, name="City Council")
    insert_meeting(conn, body_id=utc, starts_at=hours_before(36))
    insert_meeting(conn, body_id=mountain, starts_at=hours_before(36, offset="-06:00"))

    flagged = {gap.body_id for gap in gaps(conn, now=NOW)}
    assert flagged == {utc}
    assert moment_of(hours_before(36, offset="-06:00")) == NOW - timedelta(hours=30)


def test_a_timestamp_with_no_offset_is_read_as_utc(conn: sqlite3.Connection) -> None:
    """The reading the job queue's own stamps use, so a local time is never assumed."""
    body = only_body(conn)
    insert_meeting(conn, body_id=body, starts_at=hours_before(36)[:19])

    assert [gap.body_id for gap in gaps(conn, now=NOW)] == [body]


def test_the_age_of_a_gap_is_whole_hours_and_whole_days(
    conn: sqlite3.Connection,
) -> None:
    """A gap opens 36 hours after the meeting, so a 50-hour-old meeting is 14 hours in."""
    body = only_body(conn)
    insert_meeting(conn, body_id=body, starts_at=hours_before(50))

    entry = entries(conn, body_id=body, now=NOW)[0]
    assert entry.hours == 14
    assert entry.days == 0
    assert entry.since == (NOW - timedelta(hours=14)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    old = only_body(conn, type="board", town="Boulder")
    insert_meeting(conn, body_id=old, starts_at=hours_before(36 + 24 * 5 + 11))
    assert entries(conn, body_id=old, now=NOW)[0].days == 5


def test_the_gaps_of_one_body_are_oldest_first(conn: sqlite3.Connection) -> None:
    body = only_body(conn)
    insert_meeting(conn, body_id=body, starts_at=hours_before(40))
    insert_meeting(conn, body_id=body, starts_at=hours_before(90))

    found = entries(conn, body_id=body, now=NOW)
    assert [entry.hours for entry in found] == [54, 4]


def test_a_body_with_no_open_gap_is_absent_from_the_counts(conn: sqlite3.Connection) -> None:
    """An empty catalog and a catalog nobody computed are different answers."""
    quiet = only_body(conn)
    loud = only_body(conn, type="board", town="Boulder")
    insert_meeting(conn, body_id=loud, starts_at=hours_before(48))

    assert counts(conn, now=NOW) == {loud: 1}
    assert quiet not in catalogs(conn, now=NOW)
    assert loud in catalogs(conn, now=NOW)


# ------------------------------------------------- the row, and no deletion --


def test_a_record_that_appears_later_drops_off_without_deleting_the_row(
    conn: sqlite3.Connection, put_artifact: PutArtifact
) -> None:
    """The brief's own check: the list loses it, the row keeps it."""
    body = only_body(conn)
    meeting = insert_meeting(conn, body_id=body, starts_at=hours_before(48))
    assert sync(conn, now=NOW).opened == 1
    row = conn.execute("SELECT * FROM missing_records").fetchone()
    assert row["state"] == OPEN
    assert row["filled_at"] is None

    minutes_record(conn, put_artifact, meeting)
    later = NOW + timedelta(hours=6)
    found = sync(conn, now=later)

    assert (found.opened, found.filled, found.reopened) == (0, 1, 0)
    assert entries(conn, body_id=body, now=later) == []
    assert catalogs(conn, now=later) == {}
    assert counts(conn, now=later) == {}

    kept = conn.execute("SELECT * FROM missing_records").fetchall()
    assert len(kept) == 1, "the row is kept, not deleted"
    assert kept[0]["id"] == row["id"]
    assert kept[0]["state"] == FILLED
    assert kept[0]["filled_at"] == later.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    assert filled_count(conn, body_id=body) == 1


def test_a_gap_that_comes_back_reopens_on_the_moment_it_first_opened(
    conn: sqlite3.Connection, put_artifact: PutArtifact
) -> None:
    body = only_body(conn)
    meeting = insert_meeting(conn, body_id=body, starts_at=hours_before(48))
    sync(conn, now=NOW)
    opened_at = conn.execute("SELECT opened_at FROM missing_records").fetchone()["opened_at"]
    record = minutes_record(conn, put_artifact, meeting)
    assert sync(conn, now=NOW).filled == 1

    conn.execute("DELETE FROM records WHERE id = ?", (record,))
    found = sync(conn, now=NOW)

    assert (found.opened, found.filled, found.reopened) == (0, 0, 1)
    row = conn.execute("SELECT * FROM missing_records").fetchone()
    assert row["state"] == OPEN
    assert row["filled_at"] is None
    assert row["opened_at"] == opened_at, "reopening keeps the moment the gap was first seen"
    assert filled_count(conn, body_id=body) == 0


def test_a_second_sync_writes_nothing_the_first_one_did_not(
    conn: sqlite3.Connection,
) -> None:
    body = only_body(conn)
    insert_meeting(conn, body_id=body, starts_at=hours_before(48))
    assert sync(conn, now=NOW).opened == 1
    before = conn.execute("SELECT * FROM missing_records").fetchall()

    assert sync(conn, now=NOW + timedelta(hours=3)).opened == 0
    after = conn.execute("SELECT * FROM missing_records").fetchall()

    assert [dict(row) for row in after] == [dict(row) for row in before]
    assert entries(conn, body_id=body, now=NOW)[0].hours == 12
    assert entries(conn, body_id=body, now=NOW + timedelta(hours=3))[0].hours == 15


def test_the_row_says_when_it_was_written_and_when_the_gap_opened(
    conn: sqlite3.Connection,
) -> None:
    """A sync that runs late writes the moment the gap opened, not the moment it looked."""
    body = only_body(conn)
    insert_meeting(conn, body_id=body, starts_at=hours_before(60))

    sync(conn, now=NOW)
    row = conn.execute("SELECT * FROM missing_records").fetchone()
    assert row["opened_at"] == (NOW - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    assert row["created_at"] is not None
    assert row["created_at"] > row["opened_at"]


# -------------------------------------------------------- the alert setting --


def test_the_alert_setting_is_stored_and_the_threshold_is_reported(
    conn: sqlite3.Connection,
) -> None:
    """Spec 12.7's "alert me after N days", stored, and nothing delivered."""
    body = only_body(conn)
    insert_meeting(conn, body_id=body, starts_at=hours_before(36 + 24 * 3 + 1))
    assert entries(conn, body_id=body, now=NOW)[0].alert_days is None
    assert entries(conn, body_id=body, now=NOW)[0].alert_due is False

    set_missing_alert_days(conn, body, 3)
    entry = entries(conn, body_id=body, now=NOW)[0]
    assert (entry.alert_days, entry.days, entry.alert_due) == (3, 3, True)

    set_missing_alert_days(conn, body, 4)
    assert entries(conn, body_id=body, now=NOW)[0].alert_due is False

    set_missing_alert_days(conn, body, None)
    assert entries(conn, body_id=body, now=NOW)[0].alert_days is None


def test_a_setting_a_body_cannot_have_is_refused(conn: sqlite3.Connection) -> None:
    body = only_body(conn)
    with pytest.raises(ValueError):
        set_missing_alert_days(conn, body, -1)
    with pytest.raises(KeyError):
        set_missing_alert_days(conn, 9999, 3)


def test_the_three_record_kinds_are_the_ones_the_schema_accepts(
    conn: sqlite3.Connection,
) -> None:
    """Only minutes open a row today; the other two are accepted, not invented."""
    body = only_body(conn)
    meeting = insert_meeting(conn, body_id=body, starts_at=hours_before(48))
    for kind in ("agenda", "packet", "minutes"):
        conn.execute(
            "INSERT INTO missing_records (meeting_id, kind, opened_at) VALUES (?, ?, ?)",
            (meeting, kind, NOW.isoformat()),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO missing_records (meeting_id, kind, opened_at) VALUES (?, 'transcript', ?)",
            (meeting, NOW.isoformat()),
        )
