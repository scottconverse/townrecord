"""The two missing-record routes (spec 9.5, 13.1).

The catalog is read at a moment the test states, so an age in an assertion is
arithmetic and never the wall clock's answer. Both routes need the token, which
the application-wide check gives every route; the 401 assertions here say so for
these two by name.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from townrecord import artifacts
from townrecord.api.tokens import SCOPE_READ, create_token
from townrecord.repo import (
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_record,
    set_missing_alert_days,
)

#: The offset the bodies here publish their meetings in, so a published local
#: time and the instant it names are two different strings (spec 16.2).
MOUNTAIN = timezone(timedelta(hours=-6))

#: The moment the routes are held at. Both routes ask ``utcnow`` for the moment
#: they answer at, so a test can make an age a fact of the test.
NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def at(hours: float) -> str:
    """A published start whose instant is that many hours before NOW."""
    local = (NOW - timedelta(hours=hours)).astimezone(MOUNTAIN)
    return local.strftime("%Y-%m-%dT%H:%M:%S-06:00")


@dataclass(frozen=True)
class Seeded:
    """Two bodies, three meetings and a read token."""

    body: int
    quiet_body: int
    old_meeting: int
    fresh_meeting: int
    quiet_meeting: int
    token: str


@dataclass(frozen=True)
class Api:
    """A running application, the rows it answers about, and a read token."""

    client: TestClient
    seeded: Seeded
    headers: dict[str, str]


@pytest.fixture
def seeded(conn: sqlite3.Connection) -> Seeded:
    """A council with a meeting past the rule and one inside it, and a body of no kind."""
    city = insert_jurisdiction(conn, type="city", name="Longmont")
    body = insert_body(conn, jurisdiction_id=city, name="City Council", type="council")
    quiet_body = insert_body(conn, jurisdiction_id=city, name="Planning Commission")
    old_meeting = insert_meeting(
        conn, body_id=body, title="City Council Regular Session", starts_at=at(48)
    )
    fresh_meeting = insert_meeting(
        conn, body_id=body, title="City Council Study Session", starts_at=at(12)
    )
    quiet_meeting = insert_meeting(
        conn, body_id=quiet_body, title="Planning Commission Hearing", starts_at=at(48)
    )
    return Seeded(
        body=body,
        quiet_body=quiet_body,
        old_meeting=old_meeting,
        fresh_meeting=fresh_meeting,
        quiet_meeting=quiet_meeting,
        token=create_token(conn, "reader", SCOPE_READ),
    )


@pytest.fixture
def api(client: TestClient, seeded: Seeded, monkeypatch: pytest.MonkeyPatch) -> Iterator[Api]:
    """A running application, held at NOW, over a database holding the two bodies."""
    monkeypatch.setattr("townrecord.api.missing.utcnow", lambda: NOW)
    yield Api(client=client, seeded=seeded, headers={"Authorization": f"Bearer {seeded.token}"})


def one_body(api: Api) -> dict:
    """The per-body catalog of the council, as the API answers it."""
    response = api.client.get(f"/v1/bodies/{api.seeded.body}/missing", headers=api.headers)
    assert response.status_code == 200
    return response.json()


def every_body(api: Api) -> dict:
    """The whole-body listing, as the API answers it."""
    response = api.client.get("/v1/bodies", headers=api.headers)
    assert response.status_code == 200
    return response.json()


# ------------------------------------------------------------------ the token --


def test_both_routes_are_401_without_a_token(client: TestClient, seeded: Seeded) -> None:
    for path in ("/v1/bodies", f"/v1/bodies/{seeded.body}/missing"):
        assert client.get(path).status_code == 401, path
        assert client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401, path


def test_the_token_lets_the_catalog_through(api: Api) -> None:
    assert one_body(api)["body_id"] == api.seeded.body


# ---------------------------------------------------------------- the catalog --


def test_the_catalog_says_what_one_body_is_missing_and_since_when(api: Api) -> None:
    body = one_body(api)

    assert body["body_name"] == "City Council"
    assert body["body_type"] == "council"
    assert body["flagged"] is True
    assert body["count"] == 1
    assert body["filled_count"] == 0
    assert body["notes"] == []
    assert body["missing"] == [
        {
            "meeting_id": api.seeded.old_meeting,
            "meeting_title": "City Council Regular Session",
            "kind": "minutes",
            "starts_at": at(48),
            "since": (NOW - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "missing_hours": 12,
            "missing_days": 0,
            "alert_days": None,
            "alert_due": False,
        }
    ]


def test_a_meeting_inside_the_36_hours_is_not_in_the_catalog(api: Api) -> None:
    listed = {record["meeting_id"] for record in one_body(api)["missing"]}

    assert listed == {api.seeded.old_meeting}
    assert api.seeded.fresh_meeting not in listed


def test_a_body_whose_kind_nobody_stated_is_not_flagged_and_says_why(api: Api) -> None:
    """The brief's own check: no stated kind is not silently a council (rule F)."""
    response = api.client.get(f"/v1/bodies/{api.seeded.quiet_body}/missing", headers=api.headers)
    assert response.status_code == 200
    body = response.json()

    assert body["body_type"] is None
    assert body["flagged"] is False
    assert body["count"] == 0
    assert body["missing"] == []
    assert len(body["notes"]) == 1
    assert "Nobody has stated what kind of body this is" in body["notes"][0]
    assert "council, a commission, a board, an authority" in body["notes"][0]


def test_a_body_stated_to_be_none_of_the_four_says_that_instead(
    conn: sqlite3.Connection, api: Api
) -> None:
    """``'other'`` is a stated answer, which is a different fact from no answer."""
    conn.execute("UPDATE bodies SET type = 'other' WHERE id = ?", (api.seeded.quiet_body,))

    response = api.client.get(f"/v1/bodies/{api.seeded.quiet_body}/missing", headers=api.headers)
    body = response.json()

    assert body["flagged"] is False
    assert body["missing"] == []
    assert len(body["notes"]) == 1
    assert "stated to be 'other'" in body["notes"][0]


def test_an_unknown_body_is_a_404_with_a_plain_message(api: Api) -> None:
    response = api.client.get("/v1/bodies/9999/missing", headers=api.headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "There is no body 9999."


# ------------------------------------------------------- the count and the row --


def test_the_summary_count_matches_the_per_body_catalogs(api: Api) -> None:
    """The brief's own check, over every body and not only the one that is missing."""
    found = every_body(api)

    assert found["count"] == 1
    by_id = {body["id"]: body for body in found["bodies"]}
    assert set(by_id) == {api.seeded.body, api.seeded.quiet_body}
    assert by_id[api.seeded.body]["missing_count"] == 1
    assert by_id[api.seeded.body]["oldest_days"] == 0
    assert by_id[api.seeded.quiet_body]["missing_count"] == 0
    assert by_id[api.seeded.quiet_body]["oldest_days"] is None

    counted = {
        body_id: api.client.get(f"/v1/bodies/{body_id}/missing", headers=api.headers).json()[
            "count"
        ]
        for body_id in by_id
    }
    assert sum(1 for count in counted.values() if count) == found["count"]
    for body_id, body in by_id.items():
        assert body["missing_count"] == counted[body_id], f"body {body_id} disagrees"


def test_a_record_that_appears_drops_off_the_catalog_and_the_count(
    conn: sqlite3.Connection, api_storage_root: Path, api: Api
) -> None:
    assert one_body(api)["count"] == 1
    assert every_body(api)["count"] == 1

    stored = artifacts.store(conn, api_storage_root, "minutes", b"%PDF-1.4\n% adopted\n", "pdf")
    insert_record(conn, meeting_id=api.seeded.old_meeting, kind="minutes", artifact_id=stored.id)

    body = one_body(api)
    assert (body["count"], body["missing"], body["filled_count"]) == (0, [], 1)
    assert every_body(api)["count"] == 0

    kept = conn.execute("SELECT COUNT(*) AS n FROM missing_records").fetchone()["n"]
    assert kept == 1, "the row is kept, not deleted"
    assert conn.execute("SELECT state FROM missing_records").fetchone()["state"] == "filled"


# ------------------------------------------------------------ the alert setting --


def test_the_alert_threshold_is_reported_and_nothing_is_delivered(
    conn: sqlite3.Connection, api: Api
) -> None:
    """Spec 12.7's setting, stored and reported; the delivery is a later unit."""
    insert_meeting(
        conn,
        body_id=api.seeded.body,
        title="City Council Special Session",
        starts_at=at(36 + 24 * 5),
    )
    set_missing_alert_days(conn, api.seeded.body, 2)

    body = one_body(api)
    assert body["alert_days"] == 2
    due = [record for record in body["missing"] if record["alert_due"]]
    assert [record["meeting_title"] for record in due] == ["City Council Special Session"]
    assert due[0]["alert_days"] == 2
    assert due[0]["missing_days"] == 5
    assert "no alert is delivered" in body["notes"][0]

    found = {row["id"]: row for row in every_body(api)["bodies"]}
    assert found[api.seeded.body]["alert_days"] == 2
    assert found[api.seeded.body]["alert_due"] is True
    assert found[api.seeded.body]["oldest_days"] == 5
