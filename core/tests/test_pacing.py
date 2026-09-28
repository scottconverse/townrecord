"""The one pace between the YouTube requests this process makes (spec 8.10).

Spec 8.10 says "Pace every request". The measured live run of 2026-09-27 made
7 captures and 7 status asks in 80 seconds from one home connection and YouTube
answered HTTP 429 to 6 of the 7 captures, because the only pacing there was was
inside a single yt-dlp command. These tests are the pace itself: the minimum gap
between two requests, the longer hold after a 429 that every YouTube job waits
on, and the state file that keeps the hold across a restart.

No test sleeps on the wall clock: the clock and the sleeper are injected
(PROJECT-BRIEF rule 4).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from townrecord.jobs.queue import stamp as queue_stamp
from townrecord.pacing import (
    DEFAULT_MINIMUM_GAP_S,
    DEFAULT_RATE_LIMIT_BACKOFF_S,
    PACE_FILE_NAME,
    PACE_VERSION,
    RATE_LIMIT_MARKERS,
    Pacer,
    PaceSettings,
    rate_limit_marker,
    stamp_text,
)

from .capture.fakes import RATE_LIMIT_STDERR

START = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


class StepClock:
    """A clock a sleeper moves forward, so a wait ends instead of sleeping."""

    def __init__(self, start: datetime = START) -> None:
        self.now = start
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now = self.now + timedelta(seconds=seconds)


def a_pacer(**kwargs: object) -> tuple[Pacer, StepClock]:
    """A pacer on a clock the test's sleep moves, with that clock."""
    clock = StepClock()
    return Pacer(clock=clock, sleep=clock.sleep, **kwargs), clock  # type: ignore[arg-type]


# -- the minimum gap -------------------------------------------------------


def test_the_first_request_waits_for_nothing() -> None:
    """A process that has asked YouTube nothing has nothing to wait for."""
    pace, clock = a_pacer()

    assert pace.wait(what="a caption capture") == 0.0
    assert clock.slept == []


def test_a_second_request_waits_the_minimum_gap() -> None:
    """The whole point of the unit: two requests, one gap between them."""
    pace, clock = a_pacer()

    pace.wait(what="a caption capture")
    waited = pace.wait(what="a status ask")

    assert waited == DEFAULT_MINIMUM_GAP_S == 10.0
    assert clock.slept == [DEFAULT_MINIMUM_GAP_S]


def test_two_requests_at_the_same_moment_are_a_gap_apart() -> None:
    """The slot is taken before the wait, so two threads are not side by side.

    Two jobs of spec 16.1 run at once on two worker threads, and a pace that
    read the clock and then slept would let both through together.
    """
    pace, _clock = a_pacer()
    waited: list[float] = []
    ready = threading.Barrier(2)

    def ask() -> None:
        ready.wait(timeout=5.0)
        waited.append(pace.wait(what="a caption capture"))

    threads = [threading.Thread(target=ask) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    assert sorted(waited) == [0.0, DEFAULT_MINIMUM_GAP_S]


def test_a_zero_gap_turns_the_pace_off() -> None:
    """Zero is a setting a person debugging one request wants, so it means zero."""
    pace, clock = a_pacer(settings=PaceSettings(minimum_gap_s=0.0))

    pace.wait(what="a caption capture")
    assert pace.wait(what="a status ask") == 0.0
    assert clock.slept == []


# -- the hold after a 429 --------------------------------------------------


def test_a_rate_limit_holds_every_request_that_follows() -> None:
    """A 429 is YouTube answering for the address, so every job waits with it."""
    pace, clock = a_pacer()

    until = pace.rate_limited(what="a caption capture", marker="http error 429")

    assert until == START + timedelta(seconds=DEFAULT_RATE_LIMIT_BACKOFF_S)
    assert pace.held_for() == DEFAULT_RATE_LIMIT_BACKOFF_S
    assert "rate limited" in pace.held_by()
    assert "http error 429" in pace.held_by()

    # Fifteen minutes is not a wait, it is a different answer. A caller with no
    # job in hand has nothing to hand back to the queue, so it is told plainly
    # instead of being made to sit there (spec 16.1, 16.3). The pacer's own
    # name for that refusal is compared as text rather than imported, because
    # the assertion above it has to fail as an AssertionError on the tree
    # before this change and not as an ImportError (PROJECT-BRIEF rule 11b).
    with pytest.raises(RuntimeError) as caught:
        pace.wait(what="a status ask")

    assert type(caught.value).__name__ == "PaceHeld"
    assert "a caption capture was rate limited (http error 429)" in str(caught.value)
    assert "until 12:15 UTC" in str(caught.value)
    assert clock.slept == []


def test_a_hold_is_never_shortened() -> None:
    """A job's own shorter retry must not cut an address-wide hold short."""
    pace, _clock = a_pacer()

    pace.rate_limited(what="a caption capture", marker="http error 429")
    pace.rate_limited(what="an audio download", marker="http error 429", delay_s=5.0)

    assert pace.held_for() == DEFAULT_RATE_LIMIT_BACKOFF_S


def test_a_jobs_own_longer_retry_extends_the_hold() -> None:
    """And the other way round: the longest wait is the one everybody keeps."""
    pace, _clock = a_pacer()

    pace.rate_limited(what="a caption capture", marker="http error 429", delay_s=1800.0)

    assert pace.held_for() == 1800.0


def test_the_hold_survives_a_restart(tmp_path: Path) -> None:
    """A restart must not walk straight back into the same 429 (spec 8.6)."""
    path = tmp_path / PACE_FILE_NAME
    pace, _clock = a_pacer(state_path=path)
    pace.rate_limited(what="a caption capture", marker="http error 429")

    restarted, clock = a_pacer(state_path=path)

    # Read the file before the wait: a wait takes a new slot and writes it,
    # so a later read would show this process's own next request, not the
    # hold the previous one left behind.
    assert restarted.next_allowed_at() == START + timedelta(seconds=DEFAULT_RATE_LIMIT_BACKOFF_S)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["version"] == PACE_VERSION
    assert written["next_allowed_at"] == stamp_text(START + timedelta(seconds=900))

    # The restarted process does not sleep the hold it inherited either, and it
    # says how long is left the same way.
    with pytest.raises(RuntimeError) as caught:
        restarted.wait(what="a status ask")

    assert type(caught.value).__name__ == "PaceHeld"
    assert "until 12:15 UTC" in str(caught.value)
    assert clock.slept == []


def test_a_pace_file_that_cannot_be_read_is_ignored(tmp_path: Path) -> None:
    """A file nobody can read is not a failed request."""
    path = tmp_path / PACE_FILE_NAME
    path.write_text("this is not JSON", encoding="utf-8")

    pace, _clock = a_pacer(state_path=path)

    assert pace.next_allowed_at() is None
    assert pace.wait(what="a caption capture") == 0.0


def test_a_pace_that_cannot_be_written_is_not_a_failed_request(tmp_path: Path) -> None:
    """The hold in memory is the one this process uses; the file is for a restart.

    So a folder that cannot be made is a warning, and the request goes out.
    """
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("", encoding="utf-8")

    pace, _clock = a_pacer(state_path=blocker / PACE_FILE_NAME)

    assert pace.wait(what="a caption capture") == 0.0
    assert pace.held_for() == DEFAULT_MINIMUM_GAP_S


# -- the two settings ------------------------------------------------------


def test_the_defaults_are_ten_seconds_and_fifteen_minutes() -> None:
    """Ten seconds is at most six requests a minute from one address."""
    assert DEFAULT_MINIMUM_GAP_S == 10.0
    assert DEFAULT_RATE_LIMIT_BACKOFF_S == 900.0
    assert PaceSettings() == PaceSettings(
        minimum_gap_s=DEFAULT_MINIMUM_GAP_S, rate_limit_backoff_s=DEFAULT_RATE_LIMIT_BACKOFF_S
    )
    assert PACE_FILE_NAME == "youtube-pace.json"


def test_the_settings_come_from_the_environment() -> None:
    """A number a user sets is the number the pace uses."""
    settings = PaceSettings.from_env(
        {"TOWNRECORD_PACE_MINIMUM_GAP": "2.5", "TOWNRECORD_PACE_RATE_LIMIT_BACKOFF": "30"}
    )

    assert settings.minimum_gap_s == 2.5
    assert settings.rate_limit_backoff_s == 30.0


@pytest.mark.parametrize("value", ["", "  ", "soon", "-1"])
def test_a_setting_that_is_not_a_number_keeps_the_default(value: str) -> None:
    """A typo in an environment variable must not turn the pace off."""
    settings = PaceSettings.from_env(
        {"TOWNRECORD_PACE_MINIMUM_GAP": value, "TOWNRECORD_PACE_RATE_LIMIT_BACKOFF": value}
    )

    assert settings.minimum_gap_s == DEFAULT_MINIMUM_GAP_S
    assert settings.rate_limit_backoff_s == DEFAULT_RATE_LIMIT_BACKOFF_S


# -- reading a 429 out of what a child printed -----------------------------


def test_only_a_429_is_read_as_a_rate_limit() -> None:
    """The marker is what the job defers on, so anything else must not match."""
    found = rate_limit_marker(RATE_LIMIT_STDERR)

    assert found in RATE_LIMIT_MARKERS
    assert rate_limit_marker("HTTP Error 404: Not Found") is None
    assert rate_limit_marker("") is None
    assert rate_limit_marker(None) is None
    assert rate_limit_marker("HTTP ERROR 429: TOO MANY REQUESTS") is not None


def test_the_stamp_is_the_queues_own_format() -> None:
    """One reader knows the pace file and the job rows (spec 16.1)."""
    assert stamp_text(START) == queue_stamp(START) == "2026-09-27T12:00:00.000000Z"
    assert stamp_text(START.replace(tzinfo=None)) == queue_stamp(START)
