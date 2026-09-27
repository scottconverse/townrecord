"""A request starts a job and returns at once (spec 16.1, decision 10 rule 1).

The route below lives in this test file only. It stands in for the capture
route of a later unit, to prove that the request does not wait for the work.
No route was added to the real application.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from townrecord.db import connect
from townrecord.jobs import DONE, JobContext, Registry, Runner, enqueue, get

#: How long the stand-in capture takes. The request must return well before this.
HANDLER_SECONDS = 0.5


def build_app(db_path: Path) -> FastAPI:
    """A throwaway app whose only route enqueues a job and returns 202."""
    app = FastAPI(title="jobs test app")

    @app.post("/v1/capture", status_code=status.HTTP_202_ACCEPTED)
    def start_capture() -> JSONResponse:
        conn = connect(db_path)
        try:
            job_id = enqueue(conn, "capture", {"url": "https://example.invalid/meeting"})
        finally:
            conn.close()
        return JSONResponse({"job_id": job_id}, status_code=status.HTTP_202_ACCEPTED)

    return app


def wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_the_route_returns_before_the_handler_finishes(
    conn: sqlite3.Connection, db_path: Path
) -> None:
    started = threading.Event()
    finished = threading.Event()
    release = threading.Event()
    seen: list[JobContext] = []

    def handler(ctx: JobContext) -> None:
        seen.append(ctx)
        started.set()
        release.wait(timeout=HANDLER_SECONDS + 2.0)
        finished.set()

    registry = Registry()
    registry.register("capture", handler)
    runner = Runner(db_path, registry=registry)
    runner.start()
    try:
        client = TestClient(build_app(db_path))
        before = time.monotonic()
        response = client.post("/v1/capture")
        elapsed = time.monotonic() - before

        assert response.status_code == status.HTTP_202_ACCEPTED
        job_id = response.json()["job_id"]
        assert started.wait(timeout=5.0), "the runner never started the job"

        # The request is over while the handler is still inside its work.
        assert not finished.is_set()
        assert elapsed < HANDLER_SECONDS / 2, f"the request waited {elapsed:.3f}s"
        assert get(conn, job_id)["state"] == "running"

        release.set()
        assert wait_for(lambda: get(conn, job_id)["state"] == DONE), "the job never finished"
        assert len(seen) == 1
        assert seen[0].payload == {"url": "https://example.invalid/meeting"}
    finally:
        release.set()
        runner.stop(timeout=5.0)
