"""How a running service says so, and how a second shell reads it (spec 16.3).

The live run of 2026-09-27 served on port 8791 at daily time 17:21, and
``townrecord status`` in another shell reported port 8190 and 06:00: the report
was describing its own caller's settings as though they were the service. These
tests are the answer to that -- one service, one claim on the app-data root, and
one file saying what that service runs with.

The claim is an operating system lock rather than a check on a process id,
because on Windows the way to ask whether a pid is alive ends it. So the test
that matters most is the one where the file is there and the lock is not: that
is a service that is gone, and it must read as nothing at all.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from datetime import time as clock_time
from pathlib import Path

from townrecord.serving import (
    SERVICE_FILE_NAME,
    SERVICE_LOCK_NAME,
    SERVICE_VERSION,
    Served,
    ServiceLock,
    clear,
    is_running,
    read,
    record,
)

STARTED = datetime(2026, 9, 27, 23, 21, 0, tzinfo=UTC)


def a_service(root: Path, **kwargs: object) -> ServiceLock:
    """A service that has claimed ``root`` with the values the live run used."""
    values: dict[str, object] = {
        "host": "127.0.0.1",
        "port": 8791,
        "daily_time": clock_time(17, 21),
        "time_zone": "America/Denver",
        "db_path": str(root / "townrecord.db"),
        "pid": 4321,
        "clock": lambda: STARTED,
    }
    values.update(kwargs)
    return record(root, **values)  # type: ignore[arg-type]


# -- the lock --------------------------------------------------------------


def test_the_lock_is_one_per_root(tmp_path: Path) -> None:
    """Two services on one app-data root: the second one does not claim it."""
    first = a_service(tmp_path)
    try:
        second = ServiceLock.acquire(tmp_path)

        assert first.held is True
        assert second.held is False
        assert second.opened is True
    finally:
        clear(first)


def test_releasing_gives_the_lock_back(tmp_path: Path) -> None:
    """A stopped service leaves the root free for the next one (spec 16.1)."""
    claim = a_service(tmp_path)

    clear(claim)

    assert claim.held is False
    again = ServiceLock.acquire(tmp_path)
    assert again.held is True
    clear(again)


def test_a_lock_never_taken_can_be_released_twice(tmp_path: Path) -> None:
    """A caller that never got the lock has nothing to give back."""
    never = ServiceLock.acquire(tmp_path / "gone")
    never.release()
    never.release()

    assert never.held is False
    assert is_running(tmp_path / "gone") is False


# -- what a second shell sees ---------------------------------------------


def test_a_held_lock_reads_as_running(tmp_path: Path) -> None:
    """The question ``status`` asks, answered by the operating system."""
    claim = a_service(tmp_path)
    try:
        assert is_running(tmp_path) is True
    finally:
        clear(claim)

    assert is_running(tmp_path) is False


def test_a_service_file_with_no_lock_behind_it_is_not_running(tmp_path: Path) -> None:
    """A service that was killed leaves the file and no lock, and that is not a service.

    This is the whole reason the claim is a lock. Asking whether a pid is alive
    would be a guess, and on Windows the way to make that guess ends the process
    it asks about.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / SERVICE_FILE_NAME).write_text(
        json.dumps({"version": 1, "pid": 4321, "host": "127.0.0.1", "port": 8791}),
        encoding="utf-8",
    )

    assert is_running(tmp_path) is False


def test_it_writes_what_the_service_runs_with(tmp_path: Path) -> None:
    """The five things the live run and the report disagreed about, written down."""
    claim = a_service(tmp_path)
    try:
        served = read(tmp_path)
        written = json.loads((tmp_path / SERVICE_FILE_NAME).read_text(encoding="utf-8"))
    finally:
        clear(claim)

    assert served == Served(
        version=SERVICE_VERSION,
        pid=4321,
        host="127.0.0.1",
        port=8791,
        daily_time="17:21",
        time_zone="America/Denver",
        db_path=str(tmp_path / "townrecord.db"),
        started_at="2026-09-27T23:21:00.000000Z",
    )
    assert served is not None
    assert served.api == "http://127.0.0.1:8791"
    assert written["version"] == SERVICE_VERSION
    assert written["pid"] == 4321


def test_the_process_id_written_is_this_ones(tmp_path: Path) -> None:
    """The number in the file is the service's own, never one a caller guessed."""
    import os

    claim = record(
        tmp_path,
        host="127.0.0.1",
        port=8190,
        daily_time=clock_time(6, 0),
        time_zone="America/Denver",
        db_path=str(tmp_path / "townrecord.db"),
    )
    try:
        served = read(tmp_path)
    finally:
        clear(claim)

    assert served is not None
    assert served.pid == os.getpid()


def test_clearing_takes_away_what_the_service_wrote(tmp_path: Path) -> None:
    """A stopped service leaves no claim and no file behind."""
    claim = a_service(tmp_path)
    assert (tmp_path / SERVICE_FILE_NAME).is_file()

    clear(claim)

    assert not (tmp_path / SERVICE_FILE_NAME).exists()
    assert read(tmp_path) is None
    assert is_running(tmp_path) is False
    # The lock file itself stays: it is the root's, not this service's, and the
    # next service locks the same one.
    assert (tmp_path / SERVICE_LOCK_NAME).is_file()


def test_a_service_that_cannot_take_the_lock_writes_nothing(tmp_path: Path) -> None:
    """The second service must not take the running one's claim away from it."""
    first = a_service(tmp_path)
    try:
        second = a_service(tmp_path, port=9999)

        assert second.held is False
        served = read(tmp_path)
        assert served is not None
        assert served.port == 8791

        # And the second one's exit takes nothing away from the first.
        clear(second)
        assert is_running(tmp_path) is True
        assert read(tmp_path) is not None
    finally:
        clear(first)


# -- reading a file that is not one ---------------------------------------


def test_nothing_written_reads_as_nothing(tmp_path: Path) -> None:
    """The first run of a service has nothing to read, which is not an error."""
    assert read(tmp_path) is None
    assert read(tmp_path / "not-a-root") is None
    assert is_running(tmp_path / "not-a-root") is False


def test_asking_whether_a_service_runs_leaves_the_disk_alone(tmp_path: Path) -> None:
    """`townrecord status` reads, and a root that is not there holds no service."""
    root = tmp_path / "never-made"

    assert is_running(root) is False
    assert read(root) is None
    assert not root.exists(), "the question made a folder no service had made"


def test_a_file_that_is_not_a_service_file_reads_as_nothing(tmp_path: Path) -> None:
    """Half a file, or somebody else's file, is never reported as a service."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / SERVICE_FILE_NAME

    path.write_text("this is not JSON", encoding="utf-8")
    assert read(tmp_path) is None

    path.write_text(json.dumps(["a list, not an object"]), encoding="utf-8")
    assert read(tmp_path) is None

    path.write_text(json.dumps({"version": 1, "pid": "not a number"}), encoding="utf-8")
    assert read(tmp_path) is None
