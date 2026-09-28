"""The JavaScript runtime yt-dlp is given (spec 8.3, 8.9).

YouTube's pages need a JavaScript runtime, and a user's computer may have no
Node. The private runtime of spec 8.9 installs one beside yt-dlp, and this is
the module that turns the interpreter of a runtime into the value of
``--js-runtimes``.

No test here looks for a program on PATH and none of them installs anything:
the runtime is a folder of files the test writes itself, so the same assertions
hold on Windows, macOS and Linux (PROJECT-BRIEF rule 11b).
"""

from __future__ import annotations

import os
from pathlib import Path

from townrecord.runtime.javascript import (
    FALLBACK_RUNTIME,
    FALLBACK_SOURCE,
    PRIVATE_RUNTIME,
    PRIVATE_SOURCE,
    JavaScriptRuntime,
    in_private_runtime,
    resolve,
)
from townrecord.runtime.tools import JAVASCRIPT_COMPANION, program_in, program_name, python_in

FOLDER = "2026.8.19"


def venv_with(root: Path, *, program: str | None) -> Path:
    """A folder shaped like a private runtime venv, with the program or without."""
    venv = root / "runtimes" / "yt-dlp" / FOLDER
    python = python_in(venv)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/fake python\n", encoding="utf-8")
    if program is not None:
        program_in(venv, program).write_text("#!/fake program\n", encoding="utf-8")
    return venv


def test_the_companion_is_the_runtime_the_spec_names() -> None:
    """Spec 8.9 names a JavaScript runtime; the constant says which package it is."""
    assert JAVASCRIPT_COMPANION.package == "deno"
    assert JAVASCRIPT_COMPANION.program == "deno"
    assert JAVASCRIPT_COMPANION.version == "2.9.7"
    assert JAVASCRIPT_COMPANION.pin == "deno==2.9.7"
    assert JAVASCRIPT_COMPANION.program == PRIVATE_RUNTIME


def test_a_deno_beside_the_interpreter_is_the_runtime_it_is_given(tmp_path: Path) -> None:
    """The full path, spelled the way yt-dlp's ``NAME:PATH`` grammar wants."""
    venv = venv_with(tmp_path, program="deno")
    deno = program_in(venv, "deno")

    found = resolve(str(python_in(venv)))

    assert found == JavaScriptRuntime(name="deno", path=str(deno), source=PRIVATE_SOURCE)
    assert found.argument == f"deno:{deno}"
    assert found.path == str(deno)


def test_a_runtime_with_no_deno_falls_back_to_the_bare_name(tmp_path: Path) -> None:
    """A user's computer with no Node and no deno still gets a command built."""
    venv = venv_with(tmp_path, program=None)

    found = resolve(str(python_in(venv)))

    assert found == JavaScriptRuntime(name=FALLBACK_RUNTIME)
    assert found.argument == FALLBACK_RUNTIME == "node"
    assert found.path == ""
    assert found.source == FALLBACK_SOURCE


def test_a_bare_interpreter_name_is_never_looked_for_on_disk(tmp_path: Path) -> None:
    """`python` is not a path, so nothing is probed beside it and nothing is guessed.

    This is what keeps a test and a machine without Node deterministic: only an
    absolute interpreter path can name a private runtime.
    """
    assert in_private_runtime("python") is None
    assert in_private_runtime(None) is None
    assert resolve("python").argument == FALLBACK_RUNTIME

    # Even when a deno really is there, a relative name does not reach it.
    venv_with(tmp_path, program="deno")
    assert in_private_runtime("python") is None


def test_a_folder_named_deno_is_not_a_runtime(tmp_path: Path) -> None:
    """The probe asks for a file: a directory of that name is not a program."""
    venv = venv_with(tmp_path, program=None)
    (program_in(venv, "deno")).mkdir()

    assert in_private_runtime(str(python_in(venv))) is None
    assert resolve(str(python_in(venv))).argument == FALLBACK_RUNTIME


def test_the_argument_names_a_program_by_its_full_path_only_when_there_is_one() -> None:
    assert JavaScriptRuntime(name="deno", path="/opt/venv/bin/deno").argument == (
        "deno:/opt/venv/bin/deno"
    )
    assert JavaScriptRuntime(name="node").argument == "node"


def test_the_runtime_file_name_is_the_one_this_system_uses(tmp_path: Path) -> None:
    """Windows spells a console script `.exe`; the probe uses the same spelling."""
    venv = venv_with(tmp_path, program="deno")
    expected = "deno.exe" if os.name == "nt" else "deno"
    assert program_name("deno") == expected
    assert program_in(venv, "deno").name == expected
