"""Shared test fixtures."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from townrecord import pacing
from townrecord.api.app import create_app
from townrecord.api.tokens import SCOPE_READ, SCOPE_READ_WRITE, create_token
from townrecord.config import Settings
from townrecord.db import connect, migrate
from townrecord.pacing import Pacer

TEST_VERSION = "0.1.0-test"

#: The moment the pace fixtures start at. It is the capture fakes' own START,
#: spelled here so this file stands alone: nothing in the tests that use it
#: depends on the wall clock.
PACE_START = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


class MovingClock:
    """A clock a sleeper moves forward, so a paced wait ends at once.

    :meth:`townrecord.pacing.Pacer.wait` sleeps and then reads the clock again.
    A sleeper that does nothing would leave the clock where it was and the
    pacer would wait forever, so this sleeper is the clock's own
    ``advance``: a test then sees the wait that happened and never sleeps
    through it (PROJECT-BRIEF rule 4).
    """

    def __init__(self, start: datetime = PACE_START) -> None:
        self.now = start
        #: Every sleep a caller took, in order. A test says a wait happened, or
        #: that it did not, from this list and never from the wall clock: a
        #: hold that must free the worker reads as an empty list here.
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now = self.now + timedelta(seconds=seconds)


class RecordingPacer(Pacer):
    """The process pace a test watches, on a clock the test's sleep moves.

    Every handler asks :func:`townrecord.pacing.pacer` for the process pace
    rather than being handed one, so a test cannot inject a fake through a
    constructor. It installs one here instead and reads what the handlers did:
    a handler that never asks to pace records nothing, which is a failing
    assertion rather than an ImportError (PROJECT-BRIEF rule 11b).
    """

    def __init__(self, **kwargs: Any) -> None:
        self.clock = MovingClock()
        super().__init__(clock=self.clock, sleep=self.clock.sleep, **kwargs)
        #: What each caller waited to do, in order, one entry per wait.
        self.waits: list[str] = []
        #: (what, delay_s) for every rate limit: None means the job left it to
        #: the pace's own back-off.
        self.holds: list[tuple[str, float | None]] = []

    def wait(self, *, what: str) -> float:
        self.waits.append(what)
        return super().wait(what=what)

    def rate_limited(self, *, what: str, marker: str = "", delay_s: float | None = None):
        self.holds.append((what, delay_s))
        return super().rate_limited(what=what, marker=marker, delay_s=delay_s)


@pytest.fixture(autouse=True)
def the_process_pace() -> Iterator[RecordingPacer]:
    """Give every test its own process pace, and take it away afterwards.

    It is autouse because pacing is process-wide by design (spec 8.10) and a
    handler a test builds by hand asks for the process pace: without this, that
    handler would ask a real pacer and a real ``time.sleep``. It is removed
    again so no test's pace leaks into the next one.
    """
    installed = RecordingPacer()
    pacing.use_pacer(installed)
    try:
        yield installed
    finally:
        pacing.use_pacer(None)


@pytest.fixture
def pace(the_process_pace: RecordingPacer) -> RecordingPacer:
    """The pace this test's handlers ask, under a name the tests read."""
    return the_process_pace


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A database file that does not exist yet."""
    return tmp_path / "townrecord.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database connection."""
    connection = connect(db_path)
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def read_token(conn: sqlite3.Connection) -> str:
    """A token with the read scope."""
    return create_token(conn, "reader", SCOPE_READ)


@pytest.fixture
def read_write_token(conn: sqlite3.Connection) -> str:
    """A token with the read_write scope."""
    return create_token(conn, "writer", SCOPE_READ_WRITE)


@pytest.fixture
def api_storage_root(tmp_path: Path) -> Path:
    """The storage root the API under test reads artifacts from.

    It is a temporary folder and never the user's own ``~/.townrecord/storage``,
    so a test cannot read or write a real capture (spec 8.6).
    """
    root = tmp_path / "api-storage"
    root.mkdir()
    return root


@pytest.fixture
def client(db_path: Path, api_storage_root: Path) -> Iterator[TestClient]:
    """A test client for the API, using a temporary database and storage root."""
    app = create_app(Settings(db_path=db_path, version=TEST_VERSION, storage_root=api_storage_root))
    with TestClient(app) as test_client:
        yield test_client
