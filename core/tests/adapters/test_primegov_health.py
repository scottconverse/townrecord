"""health(): one known recent window, and a plain sentence either way (spec 9.1)."""

from __future__ import annotations

from datetime import date

from townrecord.adapters.base import HealthResult

from .conftest import BASE_URL, FakePortal

WINDOW = {"today": date(2026, 9, 27), "window_days": 30}


def test_a_healthy_portal_reports_what_it_checked(portal: FakePortal) -> None:
    result = portal.adapter().health(**WINDOW)

    assert isinstance(result, HealthResult)
    assert result.ok is True
    assert result.meetings_found == 2
    assert result.base_url == BASE_URL
    assert result.window_from == date(2026, 8, 28)
    assert result.window_to == date(2026, 9, 27)
    assert result.sentence() == f"{BASE_URL}: 2 meetings listed from 2026-08-28 to 2026-09-27."


def test_an_unhealthy_portal_says_what_was_checked(portal: FakePortal) -> None:
    portal.archived_status = 503

    result = portal.adapter().health(**WINDOW)

    assert result.ok is False
    assert "503" in result.reason
    assert result.checked, "the report names what was checked (spec 16.3)"
    assert result.sentence().startswith(BASE_URL)
    assert "2026" in result.sentence()


def test_a_quiet_window_is_reported_not_hidden(portal: FakePortal) -> None:
    result = portal.adapter().health(today=date(2026, 3, 15), window_days=7)

    assert result.ok is False
    assert result.meetings_found == 0
    assert "No meeting was listed" in result.reason
    assert "2026-03-08" in result.sentence() and "2026-03-15" in result.sentence()


def test_a_broken_body_does_not_raise_from_health(portal: FakePortal) -> None:
    portal.upcoming = "not a list"

    result = portal.adapter().health(**WINDOW)

    assert result.ok is False
    assert result.reason and result.sentence().endswith(".")


def test_the_default_window_looks_back_a_week(portal: FakePortal) -> None:
    result = portal.adapter().health(today=date(2026, 9, 27))

    assert result.window_from == date(2026, 9, 20)
    assert result.window_to == date(2026, 9, 27)
    assert result.sentence().endswith("2026-09-20 to 2026-09-27.")
