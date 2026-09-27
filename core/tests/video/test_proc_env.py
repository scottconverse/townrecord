"""The allow-listed child environment and the no-shell runner (spec 8.3, 17).

PROJECT-BRIEF rule E: a child process gets an allow-listed environment and is
never run through a shell. These tests run one real child, because the point
of the rule is what the child actually sees.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

from townrecord import proc

#: A child that prints the environment it was given, one JSON object.
CHILD_ENV_SCRIPT = "import json, os; print(json.dumps(dict(os.environ)))"

#: Variables that must never reach a child, whatever the parent holds.
FORBIDDEN_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "YOUTUBE_API_KEY",
    "TOWNRECORD_DB",
    "AWS_SECRET_ACCESS_KEY",
    "GITHUB_TOKEN",
)


def test_only_allow_listed_names_pass() -> None:
    parent = {
        "PATH": "/usr/bin",
        "SystemRoot": "C:\\Windows",
        "TEMP": "C:\\Temp",
        "TMP": "C:\\Temp",
        "OPENAI_API_KEY": "sk-not-a-real-key",
        "YOUTUBE_API_KEY": "not-a-real-key",
        "TOWNRECORD_DB": "C:\\somewhere\\townrecord.db",
        "SOME_RANDOM_VARIABLE": "x",
    }

    env = proc.allowed_environment(parent)

    assert env["PATH"] == "/usr/bin"
    assert env["TEMP"] == "C:\\Temp"
    for name in FORBIDDEN_NAMES + ("SOME_RANDOM_VARIABLE",):
        assert name.upper() not in {key.upper() for key in env}
    assert set(env) <= set(proc.ALLOWED_ENV_NAMES)


def test_allow_list_matching_ignores_name_case() -> None:
    """Windows spells it Path, POSIX spells it PATH. Both must survive."""
    env = proc.allowed_environment({"Path": "C:\\Windows\\system32", "temp": "C:\\T"})

    assert list(env) == ["PATH", "TEMP"], "the allow-listed spelling is used"
    assert env["PATH"] == "C:\\Windows\\system32"


def test_a_secret_in_the_parent_never_reaches_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    monkeypatch.setenv("TOWNRECORD_DB", "C:\\somewhere\\townrecord.db")
    monkeypatch.setenv("PATH", "C:\\Windows\\system32")

    result = proc.run_allowlisted([sys.executable, "-c", CHILD_ENV_SCRIPT], timeout_s=60)

    assert result.returncode == 0
    child = json.loads(result.stdout.decode("utf-8"))
    seen = {key.upper() for key in child}
    assert "OPENAI_API_KEY" not in seen
    assert "TOWNRECORD_DB" not in seen
    assert "PATH" in seen, "the child still needs its path"
    assert seen <= {name.upper() for name in proc.ALLOWED_ENV_NAMES}


def test_the_child_reads_its_command_from_argv() -> None:
    result = proc.run_allowlisted([sys.executable, "-c", "print('hello')"], timeout_s=60)

    assert result.argv[0] == sys.executable
    assert result.stdout.strip() == b"hello"
    assert result.returncode == 0


def test_a_command_string_is_refused() -> None:
    """A string would go to a shell. The runner takes an argument list."""
    with pytest.raises(TypeError):
        proc.run("echo hi", timeout_s=60, env={})  # type: ignore[arg-type]


def test_run_refuses_a_shell_and_a_missing_environment() -> None:
    environment = inspect.signature(proc.run).parameters
    assert "shell" not in environment, "the shell is never a choice"
    assert "env" in environment
    assert environment["env"].default is inspect.Parameter.empty, (
        "the environment is always passed in, never inherited"
    )

    source = Path(inspect.getfile(proc)).read_text(encoding="utf-8")
    assert "shell=True" not in source


def test_a_failure_is_reported_with_its_reason() -> None:
    result = proc.run_allowlisted(
        [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"],
        timeout_s=60,
    )

    assert result.returncode == 3
    assert "boom" in result.stderr


def test_a_timeout_kills_the_child_and_is_not_a_silent_success() -> None:
    with pytest.raises(proc.ProcessTimedOut) as caught:
        proc.run_allowlisted([sys.executable, "-c", "import time; time.sleep(30)"], timeout_s=1)

    assert caught.value.timeout_s == 1
    assert "yt_dlp" in str(caught.value) or "python" in str(caught.value)
