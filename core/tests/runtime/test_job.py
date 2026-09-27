"""The `runtime_update` job kind (spec 8.9, 16.1, 16.2, 16.3).

The daily schedule enqueues one job and the job does the whole check once. What
it found is left on the job where the user can see it, and a check that could
not apply an update pauses with the plain reason instead of finishing quietly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from townrecord.jobs import PAUSED, JobPaused, get
from townrecord.jobs.registry import Registry
from townrecord.runtime.job import JOB_KIND, LANE, RuntimeUpdate, register

from .conftest import TEST_VIDEO_URL
from .fakes import (
    VENV_SPELLING,
    FakeClock,
    FakeProbe,
    FakeUV,
    checkpoint_of,
    claimed_job,
    installed,
    make_venv,
    manager_for,
    pypi_client,
    venv_python,
    write_installed,
)


def handler_for(
    app_root: Path,
    *,
    uv: FakeUV | None = None,
    latest: str = "2026.9.1",
    probe: FakeProbe | None = None,
) -> RuntimeUpdate:
    """The job handler, with the fake uv and a fake PyPI behind it."""
    return RuntimeUpdate(
        manager=manager_for(app_root, uv if uv is not None else FakeUV()),
        client=pypi_client(latest),
        probe=probe if probe is not None else FakeProbe(passed=True),
        test_video_url=TEST_VIDEO_URL,
    )


def test_the_kind_is_registered_in_the_normal_lane(app_root: Path) -> None:
    """It waits on PyPI and on uv, not on local computing, so it is a normal job."""
    registry = Registry()
    register(
        root=app_root,
        client=pypi_client(),
        probe=FakeProbe(),
        test_video_url=TEST_VIDEO_URL,
        registry=registry,
    )
    assert JOB_KIND == "runtime_update"
    assert LANE == "normal"
    assert registry.lane_for(JOB_KIND) == "normal"
    assert JOB_KIND in registry.kinds()


def test_the_job_records_what_the_check_found_where_the_user_can_see_it(
    conn: sqlite3.Connection, app_root: Path
) -> None:
    """A passing check finishes the job and leaves the new version in its record."""
    clock = FakeClock()
    ctx = claimed_job(conn, JOB_KIND, clock)
    probe = FakeProbe(passed=True)

    handler_for(app_root, uv=FakeUV(reported="2026.09.01"), probe=probe)(ctx)

    checkpoint = checkpoint_of(conn, ctx.job_id)
    assert checkpoint["checked"] == "2026.9.1"
    assert checkpoint["switched"] is True
    assert checkpoint["failed"] is False
    assert checkpoint["active"] == "2026.09.01"
    assert probe.seen == [str(venv_python(app_root / "runtimes" / "yt-dlp" / "2026.9.1"))]
    assert get(conn, ctx.job_id)["state"] != PAUSED


def test_a_failed_test_pauses_the_job_with_the_plain_reason(
    conn: sqlite3.Connection, app_root: Path
) -> None:
    """Spec 16.3: the job says why it could not apply the update, and does not skip."""
    make_venv(app_root, "2026.8.19")
    write_installed(app_root, installed("2026.8.19", VENV_SPELLING), None)
    clock = FakeClock()
    ctx = claimed_job(conn, JOB_KIND, clock)

    handler = handler_for(app_root, uv=FakeUV(reported="2026.09.01"), probe=FakeProbe(passed=False))
    with pytest.raises(JobPaused) as caught:
        handler(ctx)

    reason = str(caught.value)
    assert TEST_VIDEO_URL in reason
    assert VENV_SPELLING in reason
    assert "stayed in place" in reason

    row = get(conn, ctx.job_id)
    assert row["state"] == PAUSED
    assert row["last_error"] == reason
    checkpoint = checkpoint_of(conn, ctx.job_id)
    assert checkpoint["failed"] is True
    assert checkpoint["active"] == VENV_SPELLING


def test_a_pypi_lookup_that_fails_leaves_the_job_failed_and_not_paused(
    conn: sqlite3.Connection, app_root: Path
) -> None:
    """The lookup is a probe, not a conclusion: its failure is raised, not read as `no update`."""
    from townrecord.runtime import pypi

    clock = FakeClock()
    ctx = claimed_job(conn, JOB_KIND, clock)
    handler = RuntimeUpdate(
        manager=manager_for(app_root, FakeUV()),
        client=pypi_client(status_code=503),
        probe=FakeProbe(),
        test_video_url=TEST_VIDEO_URL,
    )

    with pytest.raises(pypi.RuntimeLookupFailed, match="503"):
        handler(ctx)
    assert get(conn, ctx.job_id)["state"] != PAUSED
