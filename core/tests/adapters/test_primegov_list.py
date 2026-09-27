"""list_meetings: both meeting lists, dedupe, date filter (spec 9.2)."""

from __future__ import annotations

import inspect
from datetime import date, datetime
from pathlib import Path

import pytest

from townrecord.adapters import primegov
from townrecord.adapters.base import AdapterError, AdapterHttpError
from townrecord.adapters.primegov import PrimeGovAdapter

from .conftest import BASE_URL, FakePortal, fixture_json, meeting_payload


def test_video_url_is_read(portal: FakePortal) -> None:
    """Spec 9.2: TownReporter's code did not read videoUrl. TownRecord must."""
    meetings = portal.adapter().list_meetings(date(2026, 9, 1), date(2026, 9, 30))

    assert [meeting.id for meeting in meetings] == [3709, 3712, 3812, 3789, 3713], (
        "two archived meetings and three of the upcoming list fall in September"
    )
    assert meetings[0].video_url == "https://youtube.com/watch?v=3qfQAkAAC9U"
    assert meetings[1].video_url == "https://youtube.com/watch?v=jhsFsEz0P5A"


def test_meeting_fields_and_documents(portal: FakePortal) -> None:
    meeting = portal.adapter().list_meetings(date(2026, 9, 8), date(2026, 9, 8))[0]

    assert meeting.id == 3709
    assert meeting.title == "City Council Regular Session"
    assert meeting.start == datetime(2026, 9, 8, 19, 0)
    assert meeting.start.tzinfo is None, "no time zone may be invented"
    assert [(d.template_id, d.compile_output_type, d.template_name) for d in meeting.documents] == [
        (16805, 3, "HTML Agenda"),
        (16805, 1, "Agenda"),
        (16807, 1, "Packet"),
    ]
    assert meeting.documents[0].publish_date == "2026-09-04T19:29:40.647"
    assert [d.meeting_id for d in meeting.documents] == [3709, 3709, 3709]


def test_documents_without_a_template_id_are_skipped() -> None:
    payload = meeting_payload(
        99,
        "2026-09-08T19:00:00",
        {"id": 1, "compileOutputType": 1, "templateName": "Agenda"},
        {"id": 2, "templateId": 7, "compileOutputType": 1, "templateName": "Agenda"},
    )
    meeting = primegov._meeting_from(payload)

    assert meeting is not None
    assert [document.template_id for document in meeting.documents] == [7]


def test_dates_filter_both_ends(portal: FakePortal) -> None:
    adapter = portal.adapter()

    assert [m.id for m in adapter.list_meetings(date(2026, 1, 1), date(2026, 12, 31))] == [
        3437,
        3560,
        3709,
        3712,
        3812,
        3789,
        3713,
        3652,
        3808,
        3810,
        3809,
    ]
    assert [m.id for m in adapter.list_meetings(date(2026, 9, 9), date(2026, 9, 30))] == [
        3712,
        3812,
        3789,
        3713,
    ]
    assert adapter.list_meetings(date(2026, 3, 1), date(2026, 3, 31)) == []


def test_upcoming_meetings_are_listed(portal: FakePortal) -> None:
    meetings = portal.adapter().list_meetings(date(2026, 9, 27), date(2026, 10, 8))

    assert 3812 in [meeting.id for meeting in meetings]
    assert meetings[-1].start == datetime(2026, 10, 8, 19, 0)


def test_a_meeting_in_both_lists_is_kept_once() -> None:
    portal = FakePortal()
    payload = meeting_payload(500, "2026-09-08T19:00:00")
    portal.archived_by_year = {2026: [payload]}
    portal.upcoming = [dict(payload, title="changed since")]

    meetings = portal.adapter().list_meetings(date(2026, 9, 1), date(2026, 9, 30))

    assert [meeting.id for meeting in meetings] == [500]
    assert meetings[0].title == "Test meeting 500"


def test_every_year_in_the_range_is_asked_for(portal: FakePortal) -> None:
    portal.adapter().list_meetings(date(2025, 12, 1), date(2026, 1, 31))

    queried = [
        request.url.params.get("year")
        for request in portal.requests
        if request.url.path.endswith("ListArchivedMeetings")
    ]
    assert queried == ["2025", "2026"]


def test_a_meeting_without_a_time_is_skipped() -> None:
    assert primegov._meeting_from({"id": 1, "title": "no time"}) is None
    assert primegov._meeting_from({"dateTime": "2026-09-08T19:00:00"}) is None
    assert primegov._meeting_from("not a meeting") is None


def test_a_backwards_range_is_refused(portal: FakePortal) -> None:
    with pytest.raises(AdapterError):
        portal.adapter().list_meetings(date(2026, 9, 30), date(2026, 9, 1))


def test_a_server_error_is_not_a_silent_success(portal: FakePortal) -> None:
    portal.archived_status = 500

    with pytest.raises(AdapterHttpError) as caught:
        portal.adapter().list_meetings(date(2026, 9, 1), date(2026, 9, 30))

    assert "500" in str(caught.value)


def test_every_request_names_the_adapter(portal: FakePortal) -> None:
    portal.adapter(version="9.9.9").list_meetings(date(2026, 9, 1), date(2026, 9, 30))

    assert portal.requests
    for request in portal.requests:
        assert (
            request.headers["user-agent"]
            == "TownRecord/9.9.9 (+https://github.com/scottconverse/townrecord)"
        )


def test_a_base_url_is_required_and_no_city_is_named(portal: FakePortal) -> None:
    with pytest.raises(ValueError):
        PrimeGovAdapter("   ", portal.client())

    source = Path(inspect.getfile(primegov)).read_text(encoding="utf-8").lower()
    assert "longmont" not in source, "the adapter must never name a city"
    assert inspect.signature(PrimeGovAdapter.__init__).parameters["base_url"].default is (
        inspect.Parameter.empty
    )


def test_the_archived_fixture_keeps_the_meetings_the_tests_need() -> None:
    payload = fixture_json("archived-meetings-2026.json")

    assert [meeting["id"] for meeting in payload] == [3437, 3560, 3709, 3712]
    assert BASE_URL.endswith(".invalid")
