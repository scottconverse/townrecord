"""The usage ledger and the estimate that comes before it (spec 11.8)."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from townrecord.ai import ledger
from townrecord.ai.ledger import (
    CALLS_PER_UNIT,
    Estimate,
    PlannedWork,
    calls_on,
    day_totals,
    estimate_calls,
    month_totals,
    record_call,
    total_for,
)

DAY = "2026-09-27"
NEXT_DAY = "2026-09-28"
NEXT_MONTH = "2026-10-01"


class TestCountingCalls:
    def test_the_ledger_counts_per_day(self, conn: sqlite3.Connection) -> None:
        """The named check: a second day does not add to the first."""
        record_call(conn, provider="mine", task="summarize", day=DAY)
        record_call(conn, provider="mine", task="summarize", day=DAY)
        record_call(conn, provider="mine", task="summarize", day=NEXT_DAY)
        assert calls_on(conn, provider="mine", task="summarize", day=DAY) == 2
        assert calls_on(conn, provider="mine", task="summarize", day=NEXT_DAY) == 1
        assert calls_on(conn, provider="mine", day=DAY) == 2

    def test_the_ledger_counts_per_provider_and_task(self, conn: sqlite3.Connection) -> None:
        record_call(conn, provider="mine", task="summarize", day=DAY)
        record_call(conn, provider="mine", task="classify", day=DAY, calls=5)
        record_call(conn, provider="yours", task="summarize", day=DAY, calls=3)
        assert calls_on(conn, provider="mine", task="classify", day=DAY) == 5
        assert calls_on(conn, provider="mine", day=DAY) == 6
        assert calls_on(conn, provider="yours", day=DAY) == 3

    def test_recording_returns_the_new_total(self, conn: sqlite3.Connection) -> None:
        assert record_call(conn, provider="mine", task="ocr", day=DAY) == 1
        assert record_call(conn, provider="mine", task="ocr", day=DAY, calls=4) == 5

    def test_a_day_with_nothing_recorded_is_zero_and_not_missing(
        self, conn: sqlite3.Connection
    ) -> None:
        assert calls_on(conn, provider="mine", task="ocr", day=DAY) == 0
        assert day_totals(conn, DAY) == {}
        assert total_for(conn, "mine") == 0

    def test_a_ledger_entry_is_a_positive_number_of_calls(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="positive number"):
            record_call(conn, provider="mine", task="ocr", day=DAY, calls=0)
        with pytest.raises(ValueError, match="positive number"):
            record_call(conn, provider="mine", task="ocr", day=DAY, calls=-1)

    def test_a_date_is_accepted_where_a_day_is(self, conn: sqlite3.Connection) -> None:
        record_call(conn, provider="mine", task="ocr", day=date(2026, 9, 27))
        assert calls_on(conn, provider="mine", day="2026-09-27") == 1


class TestDaysAndMonths:
    def test_every_provider_on_one_day_is_listed(self, conn: sqlite3.Connection) -> None:
        record_call(conn, provider="mine", task="ocr", day=DAY, calls=2)
        record_call(conn, provider="other", task="ocr", day=DAY, calls=1)
        record_call(conn, provider="mine", task="ocr", day=NEXT_DAY, calls=9)
        assert day_totals(conn, DAY) == {"mine": 2, "other": 1}

    def test_a_month_is_the_sum_of_the_days_that_exist(self, conn: sqlite3.Connection) -> None:
        record_call(conn, provider="mine", task="ocr", day=DAY, calls=2)
        record_call(conn, provider="mine", task="ocr", day=NEXT_DAY, calls=3)
        record_call(conn, provider="mine", task="ocr", day=NEXT_MONTH, calls=7)
        assert month_totals(conn, "2026-09") == {"mine": 5}
        assert month_totals(conn, "2026-10") == {"mine": 7}

    def test_a_month_can_be_read_from_a_date_in_it(self, conn: sqlite3.Connection) -> None:
        record_call(conn, provider="mine", task="ocr", day=DAY, calls=2)
        assert month_totals(conn, date(2026, 9, 27)) == {"mine": 2}

    def test_a_month_is_never_a_second_row_that_can_disagree(
        self, conn: sqlite3.Connection
    ) -> None:
        record_call(conn, provider="mine", task="ocr", day=DAY, calls=2)
        rows = conn.execute("SELECT day FROM ai_usage").fetchall()
        assert [row["day"] for row in rows] == [DAY]

    def test_a_total_is_per_provider_on_a_day_in_a_month_or_over_everything(
        self, conn: sqlite3.Connection
    ) -> None:
        record_call(conn, provider="mine", task="ocr", day=DAY, calls=2)
        record_call(conn, provider="mine", task="ocr", day=NEXT_MONTH, calls=5)
        assert total_for(conn, "mine", day=DAY) == 2
        assert total_for(conn, "mine", month="2026-09") == 2
        assert total_for(conn, "mine") == 7
        assert total_for(conn, "nobody") == 0

    def test_the_stored_day_is_the_day_of_the_area_and_not_utc(self) -> None:
        # Spec 16.2 measures a day in the area's time zone: the caller says
        # which day it is, and this module stores that day and derives nothing.
        from datetime import UTC, datetime

        evening = datetime(2026, 9, 27, 22, 30, tzinfo=UTC)
        assert ledger._day_text(evening.date()) == "2026-09-27"


class TestTheEstimate:
    def test_every_task_has_a_calls_per_unit_figure(self) -> None:
        assert set(CALLS_PER_UNIT) == {
            "classify",
            "discover",
            "align",
            "summarize",
            "answer",
            "fact-check",
            "ocr",
            "embed",
        }

    def test_the_summarize_figure_is_the_spec_s_measurement(self) -> None:
        # Spec 11.6: a four-hour council meeting draft "takes about 20 minutes
        # across nine local-model calls".
        assert CALLS_PER_UNIT["summarize"] == ("meetings", 9)

    def test_a_first_capture_of_thirty_days_is_counted_before_it_starts(self) -> None:
        estimate = estimate_calls(PlannedWork(days=30, videos=60, meetings=12, pages=900))
        assert estimate.calls["classify"] == 60
        assert estimate.calls["summarize"] == 108
        assert estimate.calls["ocr"] == 900
        assert estimate.total == 60 + 12 + 108 + 900
        sentence = estimate.sentence()
        assert "A first capture of 30 days is about" in sentence
        assert "108 summarize" in sentence

    def test_a_task_with_no_work_makes_no_calls(self) -> None:
        estimate = estimate_calls(PlannedWork(meetings=1))
        assert estimate.calls == {"align": 1, "summarize": 9}
        # One meeting is one align call and nine summarize calls, and no
        # classify, OCR or embedding work at all.
        assert "classify" not in estimate.sentence()
        assert "9 summarize" in estimate.sentence()

    def test_a_job_with_nothing_in_it_makes_no_calls(self) -> None:
        estimate = estimate_calls(PlannedWork())
        assert estimate.total == 0
        assert estimate.sentence() == "This job is about no model calls."

    def test_a_job_that_names_no_days_is_spoken_of_as_this_job(self) -> None:
        assert estimate_calls(PlannedWork(meetings=1)).sentence().startswith("This job is about")

    def test_a_negative_unit_is_read_as_no_work(self) -> None:
        assert PlannedWork(videos=-5).units("videos") == 0
        assert PlannedWork(videos=-5).units("nothing_by_that_name") == 0

    def test_an_estimate_counts_nothing_by_itself(self) -> None:
        estimate = Estimate(work=PlannedWork(days=1))
        assert estimate.total == 0
