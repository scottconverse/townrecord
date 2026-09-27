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
from typing import Any

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

#: A name no allow-list entry and no operating system uses. The parent holds
#: it with a value nothing else uses, so a child that shows it was handed the
#: parent's environment rather than the allow-list.
MARKER_NAME = "TOWNRECORD_MARKER_PARENT_ONLY"
MARKER_VALUE = "marker-only-the-parent-holds"

#: Names the operating system itself adds to a child's environment, which the
#: parent never passed and the allow-list therefore cannot contain.
#:
#: macOS: the platform's CoreFoundation runtime sets ``__CF_USER_TEXT_ENCODING``
#: in the environment of a process that links it, and the Python interpreter on
#: macOS does. The child therefore reports a name the parent was told not to
#: pass. GitHub's macos-latest job of 2026-09-27 reported it as the only name
#: the child saw beyond the allow-list (evidence: ``evidence/ci-pr5-macos.log``).
#: It describes the user's locale, and the locale names the allow-list covers
#: (LANG, LC_ALL) arrive from the parent instead.
#:
#: Linux and Windows have added no name in the runs so far. If a platform
#: starts adding one, add it here with its reason. Never widen the check.
OS_INJECTED_ENV_NAMES: frozenset[str] = frozenset({"__CF_USER_TEXT_ENCODING"})


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


def test_run_allowlisted_passes_allow_listed_names_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strict check, on what the runner hands over.

    This reads the ``env=`` that reaches :func:`townrecord.proc.run`, so it
    is about the runner's own dict and not about what a child later reports.
    Every name in it comes from the allow-list, and no other name does.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    monkeypatch.setenv("TOWNRECORD_DB", "C:\\somewhere\\townrecord.db")
    monkeypatch.setenv("PATH", "C:\\Windows\\system32")
    monkeypatch.setenv(MARKER_NAME, MARKER_VALUE)

    handed_over: dict[str, dict[str, str]] = {}
    real_run = proc.run

    def spy(argv: Any, *, timeout_s: float, env: Any, cwd: Any = None) -> Any:
        handed_over["env"] = dict(env)
        return real_run(argv, timeout_s=timeout_s, env=env, cwd=cwd)

    monkeypatch.setattr(proc, "run", spy)
    result = proc.run_allowlisted([sys.executable, "-c", "pass"], timeout_s=60)

    assert result.returncode == 0
    assert set(handed_over) == {"env"}, "run_allowlisted never reached proc.run"
    passed = handed_over["env"]
    assert set(passed) <= set(proc.ALLOWED_ENV_NAMES), (
        "run_allowlisted passed the child a name the allow-list does not hold"
    )
    assert passed == proc.allowed_environment(), "the runner passes the allow-list, and only that"
    for name in FORBIDDEN_NAMES + (MARKER_NAME,):
        assert name.upper() not in {key.upper() for key in passed}


def test_a_secret_in_the_parent_never_reaches_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    """What a real child sees: no parent-only name, and nothing unexplained.

    The child's report is the ground truth for this rule, so the check stays
    on the child. It cannot demand an exact set: the operating system adds
    names of its own (see :data:`OS_INJECTED_ENV_NAMES`). It demands that no
    name the parent alone holds arrives, and that anything beyond the
    allow-list is a name the OS is known to add.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    monkeypatch.setenv("TOWNRECORD_DB", "C:\\somewhere\\townrecord.db")
    monkeypatch.setenv("PATH", "C:\\Windows\\system32")
    monkeypatch.setenv(MARKER_NAME, MARKER_VALUE)

    result = proc.run_allowlisted([sys.executable, "-c", CHILD_ENV_SCRIPT], timeout_s=60)

    assert result.returncode == 0
    child = json.loads(result.stdout.decode("utf-8"))
    seen = {key.upper() for key in child}
    for name in FORBIDDEN_NAMES + (MARKER_NAME,):
        assert name.upper() not in seen
    assert MARKER_VALUE not in child.values(), "the parent's marker value reached the child"
    assert "PATH" in seen, "the child still needs its path"
    extra = seen - {name.upper() for name in proc.ALLOWED_ENV_NAMES}
    assert extra <= OS_INJECTED_ENV_NAMES, (
        "the child saw a name the parent did not pass and the OS is not known to add:"
        f" {sorted(extra - OS_INJECTED_ENV_NAMES)}"
    )


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
