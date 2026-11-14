"""The Windows installer's pinned TextFlowKit seed, copied into managed storage."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

VERSION = "0.1.19"
ENV = "TOWNRECORD_RUNTIME_SEED_ROOT"


def desktop_textflowkit(tool: str = "textflowkit") -> bool:
    return tool == "textflowkit" and bool(os.environ.get(ENV))


def filesystem_path(path: Path) -> Path:
    """Use extended Windows paths for file operations, without changing saved paths."""
    value = os.path.abspath(path)
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def _child(root: Path, name: str) -> Path:
    if not isinstance(name, str) or "\\" in name or ":" in name:
        raise ValueError("The runtime seed contains an invalid file path.")
    native_root = filesystem_path(root).resolve()
    path = (native_root / name).resolve()
    if not path.is_relative_to(native_root) or path == native_root:
        raise ValueError("The runtime seed contains a file outside its folder.")
    return path


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@dataclass(frozen=True)
class Seed:
    root: Path
    manifest: dict

    @property
    def version(self) -> str:
        return self.manifest["version"]

    def verify(self) -> None:
        data = self.manifest
        if (
            data.get("format") != "townrecord-runtime-seed-v1"
            or data.get("tool") != "textflowkit"
            or data.get("version") != VERSION
        ):
            raise ValueError("The bundled TextFlowKit seed has an unsupported manifest.")
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        if data.get("python_version") != python_version:
            raise ValueError("The bundled TextFlowKit seed targets a different Python version.")
        files = data.get("files")
        if not isinstance(files, dict) or not files or "Scripts/python.exe" not in files:
            raise ValueError("The bundled TextFlowKit seed has no managed interpreter.")
        if not isinstance(data.get("packages"), dict) or not data["packages"]:
            raise ValueError("The bundled TextFlowKit seed has no exact package inventory.")
        for name, expected in files.items():
            if not (
                name.startswith("Lib/site-packages/")
                or name in {"Scripts/python.exe", "Scripts/pythonw.exe"}
            ):
                raise ValueError("The bundled TextFlowKit seed contains an unsupported file.")
            path = _child(self.root, name)
            if not path.is_file() or _digest(path) != expected:
                raise ValueError(f"The bundled TextFlowKit seed file failed verification: {name}")
        native_root = filesystem_path(self.root)
        actual = {
            p.relative_to(native_root).as_posix()
            for p in native_root.rglob("*")
            if p.is_file() and p != native_root / "manifest.json"
        }
        if actual != set(files):
            raise ValueError("The bundled TextFlowKit seed contains unlisted files.")

    def copy(self, target: Path, python: str) -> None:
        self.verify()
        native_target = filesystem_path(target)
        native_target.mkdir(parents=True, exist_ok=False)
        for name, expected in self.manifest["files"].items():
            source = _child(self.root, name)
            destination = _child(target, name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if _digest(destination) != expected:
                raise ValueError(f"The copied TextFlowKit seed file failed verification: {name}")
        base = Path(python).resolve()
        if not filesystem_path(base).is_file():
            raise ValueError("The installed bundled Python interpreter is missing.")
        python_version = ".".join(map(str, sys.version_info[:3]))
        (native_target / "pyvenv.cfg").write_text(
            f"home = {base.parent}\ninclude-system-site-packages = false\n"
            f"version = {python_version}\n"
            f"executable = {base}\n",
            encoding="utf-8",
        )


def bundled_seed(tool: str) -> Seed | None:
    if not desktop_textflowkit(tool):
        return None
    root = Path(os.environ[ENV]) / "textflowkit" / VERSION
    manifest = root / "manifest.json"
    if not filesystem_path(manifest).exists():
        return None
    return Seed(root, json.loads(filesystem_path(manifest).read_text(encoding="utf-8")))
