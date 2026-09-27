"""The second managed tool: TextFlowKit, the local transcriber (spec 8.5, 8.9).

Spec 8.9 gives every tool its own venv, its own pointer and its own rollback,
and TextFlowKit is managed the same way yt-dlp is. Exactly one thing is
different, and these tests pin it down: yt-dlp is asked which version it holds
through ``python -m yt_dlp --version``, and TextFlowKit through the console
script it installs. The rest of this file checks that the generalisations the
runtime needed for a second tool really are general: the folder, the pin, the
pointer, the probe and the rollback.

Nothing here installs anything. ``uv`` is the fake of :mod:`tests.runtime.fakes`
and the probe is a fake in every test, so no model is downloaded and no video is
fetched (spec 8.10, PROJECT-BRIEF rule 7).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from townrecord.runtime import UnknownTool, tool_spec
from townrecord.runtime.manager import RuntimeNotInstalled
from townrecord.runtime.pointer import read_pointer
from townrecord.runtime.settings import TEXTFLOWKIT_PROGRAM, TEXTFLOWKIT_TOOL, TOOL_NAME
from townrecord.runtime.tools import program_in

from .conftest import TEST_VIDEO_URL
from .fakes import (
    FakeProbe,
    FakeUV,
    installed,
    make_venv,
    manager_for,
    pypi_client,
    pypi_url,
    write_installed,
)


def tool_root(app_root: Path) -> Path:
    """The folder that holds every TextFlowKit version, and the pointer."""
    return app_root / "runtimes" / TEXTFLOWKIT_TOOL


# -- installing it ---------------------------------------------------------


def test_textflowkit_is_installed_into_its_own_venv_and_pinned(app_root: Path) -> None:
    """Its own folder under the runtimes root, and its own pin."""
    uv = FakeUV(reported="0.1.6", program=TEXTFLOWKIT_PROGRAM)

    result = manager_for(app_root, uv, tool=TEXTFLOWKIT_TOOL).install("0.1.6")

    venv = app_root / "runtimes" / TEXTFLOWKIT_TOOL / "0.1.6"
    assert uv.calls[0]["argv"] == ["uv", "venv", str(venv)]
    assert uv.pins_requested == ["textflowkit==0.1.6"]
    assert result.venv == f"runtimes/{TEXTFLOWKIT_TOOL}/0.1.6"
    assert result.version == "0.1.6"
    assert (venv).is_dir()


def test_the_version_is_asked_through_the_tools_own_program(app_root: Path) -> None:
    """Spec 8.5 runs TextFlowKit as a program, so its version is read the same way."""
    uv = FakeUV(reported="0.1.6", program=TEXTFLOWKIT_PROGRAM)

    manager_for(app_root, uv, tool=TEXTFLOWKIT_TOOL).install("0.1.6")

    venv = app_root / "runtimes" / TEXTFLOWKIT_TOOL / "0.1.6"
    asked = uv.calls[-1]["argv"]
    assert asked == [str(program_in(venv, TEXTFLOWKIT_PROGRAM)), "--version"]
    # Not the module form yt-dlp is asked with, and not this process either.
    assert "-m" not in asked
    assert uv.asked == [asked[0]]


def test_the_transcription_job_gets_the_program_of_the_active_runtime(app_root: Path) -> None:
    """Spec 8.9: the program a job runs comes from the runtime, never from PATH."""
    python = make_venv(app_root, "0.1.6", tool=TEXTFLOWKIT_TOOL, program=TEXTFLOWKIT_PROGRAM)
    write_installed(
        app_root,
        installed("0.1.6", "0.1.6", tool=TEXTFLOWKIT_TOOL),
        None,
        tool=TEXTFLOWKIT_TOOL,
    )

    manager = manager_for(app_root, tool=TEXTFLOWKIT_TOOL)

    venv = app_root / "runtimes" / TEXTFLOWKIT_TOOL / "0.1.6"
    assert manager.program() == str(program_in(venv, TEXTFLOWKIT_PROGRAM))
    assert manager.program() != str(python)
    assert Path(manager.program()).is_file()
    # The interpreter is still available for the audio download, and it is the
    # venv's own python rather than whatever is running this test.
    assert manager.interpreter() == str(python)


def test_a_runtime_that_holds_no_program_is_an_error(app_root: Path) -> None:
    """A venv built but not filled is a setup to finish, not a program to run."""
    make_venv(app_root, "0.1.6", tool=TEXTFLOWKIT_TOOL)
    write_installed(
        app_root,
        installed("0.1.6", "0.1.6", tool=TEXTFLOWKIT_TOOL),
        None,
        tool=TEXTFLOWKIT_TOOL,
    )

    with pytest.raises(RuntimeNotInstalled) as raised:
        manager_for(app_root, tool=TEXTFLOWKIT_TOOL).program()

    assert TEXTFLOWKIT_PROGRAM in str(raised.value)
    assert TEXTFLOWKIT_TOOL in str(raised.value)


def test_the_two_tools_keep_their_own_folders_and_pointers(app_root: Path) -> None:
    """Two tools under one root never share a folder, a pointer or a pin."""
    uv = FakeUV(reported="0.1.6", program=TEXTFLOWKIT_PROGRAM)
    make_venv(app_root, "2026.08.19", tool=TOOL_NAME)
    write_installed(app_root, installed("2026.08.19", "2026.08.19"), None)

    manager_for(app_root, uv, tool=TEXTFLOWKIT_TOOL).check_and_update(
        TEST_VIDEO_URL, FakeProbe(), client=pypi_client("0.1.6")
    )

    assert read_pointer(app_root / "runtimes" / TEXTFLOWKIT_TOOL).active.version == "0.1.6"
    # The tool that was already there did not move.
    ytdlp = read_pointer(app_root / "runtimes" / TOOL_NAME)
    assert ytdlp.active == installed("2026.08.19", "2026.08.19")
    assert uv.pins_requested == ["textflowkit==0.1.6"]


# -- the daily check and the rollback --------------------------------------


def test_the_daily_check_reads_the_version_of_the_tool_it_manages(app_root: Path) -> None:
    """One update path, pointed at whichever tool this manager is for (spec 8.9)."""
    uv = FakeUV(reported="0.2.0", program=TEXTFLOWKIT_PROGRAM)
    calls: list[str] = []

    outcome = manager_for(app_root, uv, tool=TEXTFLOWKIT_TOOL).check_and_update(
        TEST_VIDEO_URL, FakeProbe(), client=pypi_client("0.2.0", calls=calls)
    )

    assert calls == [pypi_url(TEXTFLOWKIT_TOOL)]
    assert outcome.switched is True
    assert outcome.active == "0.2.0"
    assert TEXTFLOWKIT_TOOL in outcome.reason
    assert read_pointer(tool_root(app_root)).active.version == "0.2.0"


def test_a_new_transcriber_that_fails_its_test_is_not_made_active(app_root: Path) -> None:
    """The known-media test decides, exactly as it does for yt-dlp."""
    make_venv(app_root, "0.1.6", tool=TEXTFLOWKIT_TOOL, program=TEXTFLOWKIT_PROGRAM)
    write_installed(
        app_root,
        installed("0.1.6", "0.1.6", tool=TEXTFLOWKIT_TOOL),
        None,
        tool=TEXTFLOWKIT_TOOL,
    )
    uv = FakeUV(reported="0.2.0", program=TEXTFLOWKIT_PROGRAM)

    outcome = manager_for(app_root, uv, tool=TEXTFLOWKIT_TOOL).check_and_update(
        TEST_VIDEO_URL, FakeProbe(passed=False), client=pypi_client("0.2.0")
    )

    assert outcome.switched is False
    assert outcome.failed is True
    assert outcome.active == "0.1.6"
    assert "failed its test" in outcome.reason
    assert read_pointer(tool_root(app_root)).active.version == "0.1.6"


def test_the_transcriber_rolls_back_to_the_version_before(app_root: Path) -> None:
    """Spec 8.9: the version that was active stays installed, so one swap undoes it."""
    make_venv(app_root, "0.1.6", tool=TEXTFLOWKIT_TOOL, program=TEXTFLOWKIT_PROGRAM)
    make_venv(app_root, "0.1.5", tool=TEXTFLOWKIT_TOOL, program=TEXTFLOWKIT_PROGRAM)
    write_installed(
        app_root,
        installed("0.1.6", "0.1.6", tool=TEXTFLOWKIT_TOOL),
        installed("0.1.5", "0.1.5", tool=TEXTFLOWKIT_TOOL),
        tool=TEXTFLOWKIT_TOOL,
    )
    manager = manager_for(app_root, tool=TEXTFLOWKIT_TOOL)

    back = manager.rollback()

    assert back.version == "0.1.5"
    pointer = read_pointer(tool_root(app_root))
    assert pointer.active.version == "0.1.5"
    assert pointer.previous.version == "0.1.6"
    # The program a job now runs is the older one, from the older venv.
    assert "0.1.5" in manager.program()


# -- a name TownRecord does not manage -------------------------------------


def test_a_tool_townrecord_does_not_manage_is_refused(app_root: Path) -> None:
    """A typo never becomes a folder of its own under the user's app-data root."""
    with pytest.raises(UnknownTool) as raised:
        manager_for(app_root, tool="ffmpeg")

    assert "ffmpeg" in str(raised.value)
    # The refusal names the tools there are, so the message is actionable.
    assert TEXTFLOWKIT_TOOL in str(raised.value)
    assert TOOL_NAME in str(raised.value)


def test_both_managed_tools_have_a_spec() -> None:
    """The registry the manager reads is the whole list of what it can install."""
    assert tool_spec(TEXTFLOWKIT_TOOL).program == TEXTFLOWKIT_PROGRAM
    # TextFlowKit is asked through its program, never through a module.
    assert tool_spec(TEXTFLOWKIT_TOOL).module is None
    assert tool_spec(TOOL_NAME).module is not None
