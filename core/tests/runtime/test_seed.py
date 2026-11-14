"""The signed desktop seed must bootstrap offline without replacing a good runtime."""

import hashlib
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from townrecord import proc
from townrecord.runtime import pypi
from townrecord.runtime.manager import RuntimeInstallFailed, RuntimeManager
from townrecord.runtime.pointer import Installed, Pointer, write_pointer
from townrecord.runtime.seed import ENV, Seed, filesystem_path
from townrecord.runtime.tools import tool_spec
from townrecord.stt.textflowkit import command_prefix


@pytest.fixture
def seed(tmp_path, monkeypatch):
    root = tmp_path / "app" / "runtime-seeds" / "textflowkit" / "0.1.19"
    content = {
        "Scripts/python.exe": b"signed interpreter test fixture",
        "Lib/site-packages/textflowkit/cli.py": b"# module test fixture",
        "Lib/site-packages/textflowkit/manifest.json": b'{"resource": true}',
        "Lib/site-packages/numpy/native.pyd": b"signed native test fixture",
    }
    for name, data in content.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = {
        "format": "townrecord-runtime-seed-v1",
        "tool": "textflowkit",
        "version": "0.1.19",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
        "packages": {"textflowkit": "0.1.19", "openai-whisper": "20250625"},
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in content.items()},
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv(ENV, str(root.parent.parent))
    python = tmp_path / "app" / "python" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"installed signed base interpreter fixture")
    monkeypatch.setenv("TOWNRECORD_RUNTIME_PYTHON", str(python))
    return Seed(root, manifest)


def runner(argv, **kwargs):
    if argv[-1] == "--version":
        assert argv[1:] == ["-I", "-m", "textflowkit.cli", "--version"]
        return proc.ProcessResult(tuple(argv), 0, b"textflowkit 0.1.19\n", "")
    assert argv[1:] == ["-I", "-c", "import whisper"]
    return proc.ProcessResult(tuple(argv), 0, b"", "")


def test_offline_seed_is_verified_copied_and_probed_without_uv(seed, tmp_path, monkeypatch):
    monkeypatch.setattr(
        pypi,
        "latest_version",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            pypi.RuntimeLookupFailed("network unavailable")
        ),
    )
    manager = RuntimeManager(tmp_path / "profile", tool="textflowkit", runner=runner)
    with httpx.Client() as client:
        outcome = manager.check_and_update("its own module", lambda _: True, client=client)
    assert outcome.checked == "unknown" and outcome.requested == "0.1.19"
    assert outcome.active == "0.1.19" and outcome.switched and not outcome.failed
    assert "network unavailable" in outcome.reason
    assert "Selected bundled seed" in outcome.reason
    python = Path(manager.interpreter())
    assert python.read_bytes() == (seed.root / "Scripts/python.exe").read_bytes()
    cfg = (python.parent.parent / "pyvenv.cfg").read_text()
    assert f"home = {Path(manager.python).parent}" in cfg
    assert "include-system-site-packages = false" in cfg
    assert manager.program() == str(python)
    assert command_prefix(manager.program()) == [str(python), "-I", "-m", "textflowkit.cli"]


def test_online_checked_latest_is_distinct_from_initial_seed(seed, tmp_path, monkeypatch):
    monkeypatch.setattr(pypi, "latest_version", lambda *_args, **_kwargs: "0.1.20")
    manager = RuntimeManager(tmp_path / "profile", tool="textflowkit", runner=runner)
    with httpx.Client() as client:
        result = manager.check_and_update("module", lambda _: True, client=client)
    assert result.checked == "0.1.20" and result.requested == "0.1.19"
    assert "latest checked: 0.1.20" in result.reason


def test_seed_tampering_and_path_escape_fail_before_activation(seed, tmp_path):
    (seed.root / "Lib/site-packages/numpy/native.pyd").write_bytes(b"tampered")
    manager = RuntimeManager(tmp_path / "profile", tool="textflowkit", runner=runner)
    with pytest.raises(ValueError, match="verification"):
        manager.install("0.1.19")
    assert manager.read_pointer().active is None
    seed.manifest["files"] = {"Lib/site-packages/../../../../escape.pyd": "fake"}
    with pytest.raises(ValueError):
        seed.verify()


def test_failed_native_probe_never_activates_seed(seed, tmp_path, monkeypatch):
    def blocked(argv, **kwargs):
        if argv[-1] == "--version":
            return runner(argv, **kwargs)
        return proc.ProcessResult(
            tuple(argv), 1, b"", "Application Control blocked native dependency"
        )

    manager = RuntimeManager(tmp_path / "profile", tool="textflowkit", runner=blocked)
    with pytest.raises(RuntimeInstallFailed, match="native transcription dependencies"):
        manager.install("0.1.19")
    assert manager.read_pointer().active is None


def test_future_update_failure_preserves_working_seed_and_online_checks(
    seed, tmp_path, monkeypatch
):
    manager = RuntimeManager(tmp_path / "profile", tool="textflowkit", runner=runner)
    result = manager.install("0.1.19")
    write_pointer(manager.tool_root, Pointer(active=Installed(result.version, result.venv)))
    original = Path(result.python).read_bytes()
    calls = []

    def latest(*_args, **_kwargs):
        calls.append("online lookup")
        return "0.1.20"

    monkeypatch.setattr(pypi, "latest_version", latest)
    monkeypatch.setattr(
        manager,
        "install",
        lambda _: (_ for _ in ()).throw(
            RuntimeInstallFailed("Application Control blocked new unsigned dependency")
        ),
    )
    with httpx.Client() as client:
        outcome = manager.check_and_update("module", lambda _: True, client=client)
    assert calls == ["online lookup"]
    assert outcome.failed and not outcome.switched and outcome.active == "0.1.19"
    assert outcome.checked == "0.1.20"
    assert manager.read_pointer().active.version == "0.1.19"
    assert Path(result.python).read_bytes() == original


def test_developer_console_contract_is_unchanged(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)
    python, venv = Path("C:/runtime/Scripts/python.exe"), Path("C:/runtime")
    assert tool_spec("textflowkit").version_argv(python=python, venv=venv)[-1] == "--version"
    assert "-m" not in tool_spec("textflowkit").version_argv(python=python, venv=venv)
    assert command_prefix("textflowkit") == ["textflowkit"]


def test_seed_rejects_wrong_python_and_unlisted_files(seed):
    original = seed.manifest["python_version"]
    seed.manifest["python_version"] = "0.0"
    with pytest.raises(ValueError, match="different Python"):
        seed.verify()
    seed.manifest["python_version"] = original
    (seed.root / "Lib/site-packages/unlisted.py").write_text("# unlisted")
    with pytest.raises(ValueError, match="unlisted files"):
        seed.verify()


def test_nested_package_manifest_is_covered_by_file_inventory(seed):
    seed.verify()
    del seed.manifest["files"]["Lib/site-packages/textflowkit/manifest.json"]
    with pytest.raises(ValueError, match="unlisted files"):
        seed.verify()


def test_broken_active_seed_is_not_mutated_by_dependency_repair(seed, tmp_path, monkeypatch):
    manager = RuntimeManager(tmp_path / "profile", tool="textflowkit", runner=runner)
    installed = manager.install("0.1.19")
    write_pointer(manager.tool_root, Pointer(active=Installed(installed.version, installed.venv)))
    monkeypatch.setattr(pypi, "latest_version", lambda *_args, **_kwargs: "0.1.19")
    calls = []

    def blocked(argv, **kwargs):
        calls.append(argv)
        return proc.ProcessResult(tuple(argv), 1, b"", "native import failed")

    manager.runner = blocked
    with httpx.Client() as client:
        result = manager.check_and_update("module", lambda _: True, client=client)
    assert result.failed and not result.switched
    assert result.active == "0.1.19"
    assert len(calls) == 1 and "import whisper" in calls[0]
    assert "without in-place repair" in result.reason


@pytest.mark.skipif(os.name != "nt", reason="Observed Windows MAX_PATH seed staging failure")
def test_seed_verifies_and_copies_long_torch_header_paths_without_policy_change(seed, tmp_path):
    name = "Lib/site-packages/torch/include/ATen/ops/" + "long_header_name_" * 11 + ".h"
    source = filesystem_path(seed.root / name)
    assert len(str(seed.root / name)) > 260
    source.parent.mkdir(parents=True, exist_ok=True)
    data = b"# retained package header beyond MAX_PATH\n"
    source.write_bytes(data)
    seed.manifest["files"][name] = hashlib.sha256(data).hexdigest()
    target = tmp_path / "long-profile-name" / "runtimes" / "textflowkit" / "0.1.19"
    seed.copy(target, os.environ["TOWNRECORD_RUNTIME_PYTHON"])
    assert filesystem_path(target / name).read_bytes() == data
    cfg = filesystem_path(target / "pyvenv.cfg").read_text()
    assert os.environ["TOWNRECORD_RUNTIME_PYTHON"] in cfg
    assert "\\\\?\\" not in cfg
    # Retrying a failed, unactivated setup must remove the long files as well.
    filesystem_path(seed.root / "manifest.json").write_text(json.dumps(seed.manifest))
    manager = RuntimeManager(target.parents[2], tool="textflowkit", runner=runner)
    result = manager.install("0.1.19")
    assert result.version == "0.1.19"
    assert filesystem_path(target / name).read_bytes() == data
