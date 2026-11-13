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

from townrecord import pacing
from townrecord.capture import JOB_KIND as CAPTURE_JOB_KIND
from townrecord.capture import LANE as CAPTURE_LANE
from townrecord.capture import RECHECK_JOB_KIND, TRANSCRIBE_JOB_KIND
from townrecord.capture.transcribe import LANE as TRANSCRIBE_LANE
from townrecord.config import Settings
from townrecord.jobs import QUEUED, RUNNING, JobContext, JobsSettings, claim, enqueue, get
from townrecord.records import (
    ALIGN_MEETING,
    CONNECTOR_OPERATION,
    DOWNLOAD_CONNECTOR,
    DOWNLOAD_RECORD,
    EXTRACT_PAGES,
    READ_MINUTES,
    READ_SPEAKERS,
    SYNC_CONNECTOR,
    SYNC_PRIMEGOV,
)
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
    READ_MINUTES,
    READ_SPEAKERS,
    CAPTURE_JOB_KIND,
    RECHECK_JOB_KIND,
    TRANSCRIBE_JOB_KIND,
    WATCH_CHANNEL_JOB_KIND,
    RUNTIME_UPDATE_KIND,
    SYNC_CONNECTOR,
    DOWNLOAD_CONNECTOR,
    CONNECTOR_OPERATION,
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
    assert len(kinds()) == len(EXPECTED_KINDS) == 14

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


def test_the_service_installs_one_pace_for_the_whole_process(settings: Settings) -> None:
    """One service, one pace, one state file, whatever job asks (spec 8.10).

    Every handler asks :func:`townrecord.pacing.pacer` rather than being handed
    a pace, so this install is what makes a capture on the normal lane and a
    transcription on the heavy lane wait for each other.
    """
    service = build_service(settings, clock=lambda: BEFORE_THE_DAY)

    installed = pacing.pacer()
    assert installed is service.pacer
    assert installed.state_path == settings.runtime_root / pacing.PACE_FILE_NAME
    assert installed.settings.minimum_gap_s == pacing.DEFAULT_MINIMUM_GAP_S

    service.close()
    assert pacing.forget_pacer(installed) is False, "the service kept its pace after closing"


# -- a runtime installed while the service runs ------------------------------


def test_a_runtime_installed_after_start_is_used_without_a_restart(settings: Settings) -> None:
    """The first run installs yt-dlp and TextFlowKit while the service is up.

    The live run of 2026-11-08 built the service before either runtime existed,
    so every capture ran this process's interpreter, which has no yt_dlp, until
    the service was restarted. The handlers follow the active runtime instead.
    """

    from townrecord.runtime.settings import TEXTFLOWKIT_PROGRAM, TEXTFLOWKIT_TOOL
    from townrecord.runtime.tools import program_in
    from townrecord.service import WaitsForRuntime

    from .runtime.fakes import installed, make_venv, write_installed

    service = build_service(settings, clock=lambda: BEFORE_THE_DAY)
    capture = service.handlers[CAPTURE_JOB_KIND]
    # Before the install there is no yt-dlp, so the capture waits for it
    # rather than running this process's interpreter.
    assert isinstance(capture.current(), WaitsForRuntime)

    root = settings.runtime_root
    python = make_venv(root, "2026.11.1")
    write_installed(root, installed("2026.11.1", "2026.11.1"), None)
    make_venv(root, "0.1.19", tool=TEXTFLOWKIT_TOOL, program=TEXTFLOWKIT_PROGRAM)
    write_installed(
        root, installed("0.1.19", "0.1.19", tool=TEXTFLOWKIT_TOOL), None, tool=TEXTFLOWKIT_TOOL
    )
    program = program_in(root / "runtimes" / TEXTFLOWKIT_TOOL / "0.1.19", TEXTFLOWKIT_PROGRAM)

    assert capture.current().interpreter == str(python)
    assert service.handlers[RECHECK_JOB_KIND].current().interpreter == str(python)
    transcribe = service.handlers[TRANSCRIBE_JOB_KIND].current()
    assert transcribe.ytdlp_interpreter == str(python)
    assert transcribe.textflowkit_program == str(program)
    watch = service.handlers[WATCH_CHANNEL_JOB_KIND].current()
    assert watch.ladder.ytdlp.interpreter == str(python)
    service.close()


def test_the_update_job_updates_the_tool_its_payload_names(settings: Settings) -> None:
    """One runtime_update kind, and the payload says which tool (spec 8.9).

    Nothing installed TextFlowKit in the live run, because the one handler was
    built for yt-dlp and ignored the payload's ``tool``.
    """
    from townrecord.runtime.settings import TEXTFLOWKIT_TOOL, TOOL_NAME

    service = build_service(settings, clock=lambda: BEFORE_THE_DAY)
    update = service.handlers[RUNTIME_UPDATE_KIND]

    assert update.for_payload({"tool": TEXTFLOWKIT_TOOL}).tool == TEXTFLOWKIT_TOOL
    assert update.for_payload({"tool": TOOL_NAME}).tool == TOOL_NAME
    assert update.for_payload(None).tool == TOOL_NAME
    with pytest.raises(ValueError, match="does not manage"):
        update.for_payload({"tool": "left-pad"})
    service.close()


def test_a_watch_waits_for_the_yt_dlp_runtime_instead_of_failing(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    """A watch that runs before yt-dlp is installed waits for it (spec 8.9).

    The live run of 2026-11-09 queued a watch at start, beside the first
    ``runtime_update``. The watch ran nine seconds before the install finished,
    the channel's feed answered 404, and the last rung ran yt-dlp with this
    process's interpreter, which has no yt_dlp: the job failed with "No module
    named yt_dlp" and the channel was not watched. Nothing here is faked but
    the feed's 404, which is what YouTube answered for both Longmont channels.
    """
    import httpx

    from townrecord.jobs import Runner
    from townrecord.records import portal
    from townrecord.repo import insert_jurisdiction, insert_source
    from townrecord.video.watch import LANE as WATCH_LANE

    city = insert_jurisdiction(conn, type="city", name="Longmont")
    source = insert_source(
        conn,
        jurisdiction_id=city,
        type=portal.VIDEO_CHANNEL,
        origin="https://www.youtube.com/channel/UCH5ilm2TwUm2C9-pc8ejNKw",
        suggested_by="a person",
        reason="the city's channel",
        status="accepted",
        body_id=None,
        settings="{}",
    )
    feed_404 = httpx.MockTransport(lambda request: httpx.Response(404))
    client = httpx.Client(transport=feed_404, trust_env=False)
    service = build_service(settings, clock=lambda: BEFORE_THE_DAY, client=client)
    try:
        job_id = enqueue(
            conn, WATCH_CHANNEL_JOB_KIND, {"source_id": source}, registry=service.registry
        )
        conn.commit()
        Runner(settings.db_path, registry=service.registry, clock=lambda: BEFORE_THE_DAY).run_once(
            WATCH_LANE
        )
    finally:
        service.close()

    row = get(conn, job_id)
    assert row["state"] == QUEUED, row["last_error"]
    assert "yt-dlp" in row["last_error"] and "not installed" in row["last_error"]
    assert row["run_after"] > BEFORE_THE_DAY.isoformat()


def test_a_capture_waits_for_the_yt_dlp_runtime_instead_of_using_this_interpreter(
    settings: Settings, conn: sqlite3.Connection
) -> None:
    """A capture that runs before yt-dlp is installed waits a minute for it (spec 8.9).

    The live run of 2026-11-10 (live-m1h) started capture job 11 at 18:32:41Z,
    five seconds before the first ``runtime_update`` finished. The job asked the
    video's status with this process's interpreter, which has no yt_dlp, read
    the answer as ``unknown`` and waited the readiness hour of spec 8.2, so
    meeting 4 aligned an hour late. With no runtime there is no yt-dlp to ask:
    the job waits for the install, as a watch does, and asks nothing.
    """
    from townrecord.jobs import Runner
    from townrecord.records import portal
    from townrecord.repo import insert_jurisdiction, insert_source
    from townrecord.repo.meetings import insert_video

    from .capture.fakes import FakeYtDlp

    city = insert_jurisdiction(conn, type="city", name="Longmont")
    source = insert_source(
        conn,
        jurisdiction_id=city,
        type=portal.VIDEO_CHANNEL,
        origin="https://www.youtube.com/channel/UCH5ilm2TwUm2C9-pc8ejNKw",
        suggested_by="a person",
        reason="the city's channel",
        status="accepted",
        body_id=None,
        settings="{}",
    )
    video = insert_video(conn, source_id=source, platform_video_id="uHFuCfd5MVQ")
    conn.commit()
    fake = FakeYtDlp(platform_video_id="uHFuCfd5MVQ")
    service = build_service(settings, clock=lambda: BEFORE_THE_DAY, capture_runner=fake)
    try:
        job_id = enqueue(conn, CAPTURE_JOB_KIND, {"video_id": video}, registry=service.registry)
        conn.commit()
        Runner(settings.db_path, registry=service.registry, clock=lambda: BEFORE_THE_DAY).run_once(
            CAPTURE_LANE
        )
    finally:
        service.close()

    row = get(conn, job_id)
    assert fake.calls == []
    assert row["state"] == QUEUED, row["last_error"]
    assert "yt-dlp" in row["last_error"] and "not installed" in row["last_error"]
    waited = datetime.fromisoformat(row["run_after"]) - BEFORE_THE_DAY
    assert waited.total_seconds() <= 120


def test_recheck_and_transcription_also_wait_for_the_yt_dlp_runtime(settings: Settings) -> None:
    """Every job that runs yt-dlp waits for its runtime, not only the watch and capture."""
    from townrecord.service import WaitsForRuntime

    service = build_service(settings, clock=lambda: BEFORE_THE_DAY)
    try:
        for kind in (CAPTURE_JOB_KIND, RECHECK_JOB_KIND, TRANSCRIBE_JOB_KIND):
            assert isinstance(service.handlers[kind].current(), WaitsForRuntime), kind
    finally:
        service.close()
