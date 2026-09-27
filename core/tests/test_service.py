"""The one core the desktop shell starts (spec 14.2, 16.1, 8.6).

``build_service`` is the whole wiring: every job kind that exists, with the
storage root, the runtime root and the programs coming from the settings, and a
shutdown that leaves a running job claimable. These tests are the ones that
fail when a kind is added to the code and not to the service, which is the
failure this unit exists to make impossible to miss.
"""

from __future__ import annotations

import importlib
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from datetime import time as clock_time
from pathlib import Path

import pytest

from townrecord.capture import JOB_KIND as CAPTURE_JOB_KIND
from townrecord.capture import LANE as CAPTURE_LANE
from townrecord.capture import TRANSCRIBE_JOB_KIND
from townrecord.capture.transcribe import LANE as TRANSCRIBE_LANE
from townrecord.config import Settings
from townrecord.jobs import QUEUED, RUNNING, JobContext, JobsSettings, claim, enqueue, get
from townrecord.records import ALIGN_MEETING, DOWNLOAD_RECORD, EXTRACT_PAGES, SYNC_PRIMEGOV
from townrecord.runtime import JOB_KIND as RUNTIME_UPDATE_KIND
from townrecord.service import KIND_MODULES, build_service, kinds
from townrecord.video.watch import JOB_KIND as WATCH_CHANNEL_JOB_KIND

#: Every job kind that exists, spelled out. A new kind has to be added here as
#: well as to the service, and that is the point: the list is the spec's, not
#: the code's.
EXPECTED_KINDS = (
    SYNC_PRIMEGOV,
    DOWNLOAD_RECORD,
    EXTRACT_PAGES,
    ALIGN_MEETING,
    CAPTURE_JOB_KIND,
    TRANSCRIBE_JOB_KIND,
    WATCH_CHANNEL_JOB_KIND,
    RUNTIME_UPDATE_KIND,
)

#: 06:00 local, and the clock below is 04:00 in Denver, so the schedule has
#: nothing to do while these tests run.
DAILY_TIME = clock_time(6, 0)

#: A moment before the daily time, so the schedule's thread writes nothing
#: while a test is looking at the queue.
BEFORE_THE_DAY = datetime(2026, 9, 27, 10, 0, tzinfo=UTC)


@pytest.fixture
def settings(db_path: Path, tmp_path: Path) -> Settings:
    """Settings pointing every path at a temporary folder (spec 8.6)."""
    return Settings(
        db_path=db_path,
        storage_root=tmp_path / "storage",
        runtime_root=tmp_path / "runtime",
        time_zone="America/Denver",
        daily_time=DAILY_TIME,
    )


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Wait for a condition another thread brings about."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_every_job_kind_that_exists_is_registered(settings: Settings) -> None:
    service = build_service(settings, clock=lambda: BEFORE_THE_DAY)

    assert service.registry.kinds() == tuple(sorted(EXPECTED_KINDS))
    assert set(service.registry.kinds()) == set(kinds())
    assert len(kinds()) == 8

    # Each kind is a name the module the service says registers it really
    # declares, so KIND_MODULES is a fact and not a comment.
    for kind, module_name in KIND_MODULES:
        module = importlib.import_module(module_name)
        declared = {value for value in vars(module).values() if isinstance(value, str)}
        assert kind in declared, f"{module_name} does not declare the job kind {kind!r}"

    # And every one of them has a handler: a kind wired with none is a job that
    # would sit in the queue and then fail for want of a handler.
    assert set(service.handlers) == set(EXPECTED_KINDS)
    assert all(handler is not None for handler in service.handlers.values())
    service.close()


def test_the_lanes_are_the_ones_spec_16_1_names(settings: Settings) -> None:
    """Heavy runs one job at a time, normal two, and transcribe only one."""
    service = build_service(settings, clock=lambda: BEFORE_THE_DAY)
    jobs = JobsSettings()

    assert service.registry.lane_for(CAPTURE_JOB_KIND) == CAPTURE_LANE == "normal"
    assert service.registry.lane_for(TRANSCRIBE_JOB_KIND) == TRANSCRIBE_LANE == "heavy"
    assert jobs.lane_limit("heavy") == 1
    assert jobs.lane_limit("normal") == 2
    assert jobs.kind_limit(TRANSCRIBE_JOB_KIND) == 1
    service.close()


def test_a_stopped_service_leaves_its_job_claimable(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    service = build_service(settings, clock=lambda: BEFORE_THE_DAY)
    started = threading.Event()

    def handler(ctx: JobContext) -> None:
        ctx.save_checkpoint({"part": 1})
        started.set()
        while not ctx.should_stop():
            time.sleep(0.01)
        ctx.save_checkpoint({"part": 2, "stopped_early": True})
        ctx.interrupt("The service was stopping.")

    service.registry.register("test_blocking", handler, lane="normal")
    job_id = enqueue(conn, "test_blocking", {"body": "city"}, registry=service.registry)

    service.start()
    try:
        assert wait_for(started.is_set), "the handler never started"
        assert get(conn, job_id)["state"] == RUNNING
    finally:
        assert service.stop(timeout=10.0) is True
        service.close()

    row = get(conn, job_id)
    assert row["state"] == QUEUED
    assert row["claim_token"] is None
    assert row["last_error"] == "The service was stopping."
    assert row["checkpoint"] is not None

    resumed = claim(conn, "normal", "worker-after-a-restart")
    assert resumed is not None
    assert resumed.job_id == job_id
    kept = JobContext(
        conn=conn,
        job_id=resumed.job_id,
        kind=resumed.kind,
        payload=resumed.payload,
        lane=resumed.lane,
        claim_token=resumed.token,
    )
    assert kept.checkpoint() == {"part": 2, "stopped_early": True}


def test_the_service_closes_what_it_opened(settings: Settings) -> None:
    service = build_service(settings, clock=lambda: BEFORE_THE_DAY)
    assert service.client is not None
    service.close()
    assert service.client is None
    service.close()  # closing twice is not an error
