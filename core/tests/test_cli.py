"""The ``townrecord`` command (spec 14.2, 13.1, 16.1, 16.3).

``townrecord serve`` is started here as a real process, on a port this test
picked and holds the number of, and stopped in a ``finally`` that checks the
port is free again (rule 8: a test stops only the processes it started, and
never stops one chosen by its name). Every request goes to the loopback
address; nothing here reaches the network.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from townrecord.cli import EXIT_REFUSED, main

#: The loopback address this file talks to, and the only host it uses.
HOST = "127.0.0.1"

#: How long a started service may take to answer, and to stop.
START_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 20.0


def free_port() -> int:
    """Return a port nothing is listening on, for the service to bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def nothing_listening(port: int) -> bool:
    """Return whether no process is listening on `port` any more.

    A connection that is refused, or that times out, is the answer: a live
    listener on the loopback address accepts at once. Binding the port is not a
    usable test -- a socket in TIME_WAIT, left by a connection this test itself
    opened, can refuse a fresh bind on Windows for a while after the process
    that served it is gone, and that is not a program holding the port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1.0)
        try:
            probe.connect((HOST, port))
        except OSError:
            return True
    return False


def wait_for_port_free(port: int, timeout: float = STOP_TIMEOUT_S) -> bool:
    """Wait for the port to close. True when nothing is listening on it.

    The wait is what makes this an answer about the process this test started:
    a stopped process releases its listening socket a moment after ``wait``
    returns, and a check made in that moment would call the port held.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if nothing_listening(port):
            return True
        time.sleep(0.2)
    return nothing_listening(port)


def env_for(tmp_path: Path, port: int, **extra: str) -> dict[str, str]:
    """The environment one `townrecord` process runs with: temporary paths."""
    env = dict(os.environ)
    env.update(
        {
            "TOWNRECORD_DB": str(tmp_path / "townrecord.db"),
            "TOWNRECORD_STORAGE": str(tmp_path / "storage"),
            "TOWNRECORD_RUNTIME_ROOT": str(tmp_path / "runtime"),
            "TOWNRECORD_HOST": HOST,
            "TOWNRECORD_PORT": str(port),
            "TOWNRECORD_TIME_ZONE": "America/Denver",
        }
    )
    env.update(extra)
    return env


def run_cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Run one command to completion and return what it said."""
    return subprocess.run(
        [sys.executable, "-m", "townrecord", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def wait_for_serve(port: int, process: subprocess.Popen[str], log: Path) -> None:
    """Wait until the service answers, or say what it printed instead."""
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(
                f"`townrecord serve` exited with {process.returncode} before it answered:\n"
                f"{log.read_text(encoding='utf-8', errors='replace')}"
            )
        try:
            with httpx.Client(trust_env=False, timeout=1.0) as probe:
                assert probe.get(f"http://{HOST}:{port}/v1/health").status_code == 401
            return
        except httpx.TransportError:
            time.sleep(0.1)
    raise AssertionError(
        f"`townrecord serve` did not answer on port {port} within {START_TIMEOUT_S:.0f}s:\n"
        f"{log.read_text(encoding='utf-8', errors='replace')}"
    )


@pytest.fixture
def port() -> int:
    """A port this test chose, which it proves is free before and after."""
    chosen = free_port()
    assert nothing_listening(chosen)
    return chosen


@pytest.fixture
def served(tmp_path: Path, port: int) -> Iterator[tuple[str, str]]:
    """A running `townrecord serve`, with a read token, and its stop."""
    env = env_for(tmp_path, port)
    created = run_cli(env, "token", "create", "--name", "desk", "--scope", "read")
    assert created.returncode == 0, created.stderr
    token = created.stdout.strip().splitlines()[-1]
    assert token.startswith("tr_")

    log = tmp_path / "serve.log"
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            [sys.executable, "-m", "townrecord", "serve"],
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
        )
    try:
        wait_for_serve(port, process, log)
        yield f"http://{HOST}:{port}", token
    finally:
        process.terminate()
        try:
            process.wait(timeout=STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:  # pragma: no cover - a stuck child
            process.kill()
            process.wait(timeout=STOP_TIMEOUT_S)
        assert wait_for_port_free(port), f"a process still holds port {port}"


def test_serve_refuses_a_request_with_no_token(served: tuple[str, str]) -> None:
    """Decision 10: every route needs a token, and the docs pages are routes."""
    base, token = served
    with httpx.Client(trust_env=False, timeout=10.0) as client:
        for route in ("/v1/health", "/docs", "/redoc", "/openapi.json"):
            response = client.get(f"{base}{route}")
            assert response.status_code == 401, f"{route} answered {response.status_code} unasked"
            assert response.headers.get("www-authenticate") == "Bearer"

        # A token that is not one is refused the same way.
        wrong = client.get(f"{base}/v1/health", headers={"Authorization": "Bearer tr_nope"})
        assert wrong.status_code == 401

        # And the token this service was given opens the route it is for.
        ok = client.get(f"{base}/v1/health", headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200
        assert ok.json()["status"] == "ok"

        docs = client.get(f"{base}/docs", headers={"Authorization": f"Bearer {token}"})
        assert docs.status_code == 200
        assert "swagger" in docs.text.lower()


def test_serve_reports_a_busy_port_with_the_setting_to_change(tmp_path: Path, port: int) -> None:
    """A port already in use is one plain sentence, not a stack trace."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind((HOST, port))
        taken.listen(8)
        result = run_cli(env_for(tmp_path, port), "serve")

    assert result.returncode == EXIT_REFUSED
    assert f"Port {port} is already in use" in result.stderr
    assert "TOWNRECORD_PORT" in result.stderr
    assert wait_for_port_free(port)  # nothing of the service was left behind


def test_a_created_token_is_printed_once_and_never_listed(tmp_path: Path, port: int) -> None:
    """Spec 13.1: the token is shown once, and the list shows names and scopes."""
    env = env_for(tmp_path, port)
    created = run_cli(env, "token", "create", "--name", "council", "--scope", "write")
    assert created.returncode == 0, created.stderr
    token = created.stdout.strip().splitlines()[-1]
    assert token.startswith("tr_")
    assert "read_write" in created.stdout

    listed = run_cli(env, "token", "list")
    assert listed.returncode == 0, listed.stderr
    assert token not in listed.stdout
    assert "council" in listed.stdout
    assert "read_write" in listed.stdout

    empty = run_cli(env_for(tmp_path / "empty", port), "token", "list")
    assert "No tokens have been created yet" in empty.stdout


def test_status_prints_the_report_and_its_json(tmp_path: Path, port: int) -> None:
    """Spec 16.3: real content, and "OK" is not content."""
    env = env_for(tmp_path, port)
    assert run_cli(env, "token", "create", "--name", "desk", "--scope", "read").returncode == 0

    printed = run_cli(env, "status")
    assert printed.returncode == 0, printed.stderr
    assert str(tmp_path / "townrecord.db") in printed.stdout
    assert f"http://{HOST}:{port}" in printed.stdout
    assert "Sources" in printed.stdout
    assert "America/Denver" in printed.stdout
    assert "OK" not in printed.stdout.split()

    as_json: dict[str, Any] = json.loads(run_cli(env, "status", "--json").stdout)
    assert as_json["db_present"] is True
    assert as_json["port"] == port
    assert len(as_json["registered_kinds"]) == 8
    assert as_json["jobs"] == []
    assert as_json["notes"] == []


def test_the_console_script_entry_point_runs_in_process(
    tmp_path: Path, port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``[project.scripts]`` names ``townrecord.cli:main``; it works when called."""
    for name, value in env_for(tmp_path, port).items():
        monkeypatch.setenv(name, value)

    assert main(["token", "create", "--name", "desk", "--scope", "read"]) == 0
    assert main(["token", "list"]) == 0
    assert main(["status"]) == 0
