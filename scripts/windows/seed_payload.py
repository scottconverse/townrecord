"""The one pinned Windows TextFlowKit seed and its derived signing manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import re
import shutil
from pathlib import Path

RELATIVE = Path("runtime-seeds/textflowkit/0.1.19")
ORIGINAL = Path("notices/textflowkit-seed-original-manifest.json")
REPO = Path(__file__).resolve().parents[2]
WORK = REPO / "build/windows"


def filesystem_path(path):
    """Use Windows' supported extended path syntax without a policy change."""
    resolved = str(Path(path).resolve())
    if os.name == "nt" and not resolved.startswith("\\\\?\\"):
        resolved = (
            "\\\\?\\UNC\\" + resolved[2:]
            if resolved.startswith("\\\\")
            else "\\\\?\\" + resolved
        )
    return Path(resolved)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, data):
    Path(path).write_text(
        json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def file_map(root):
    return {
        file.relative_to(root).as_posix(): digest(file)
        for file in sorted(root.rglob("*"))
        if file.is_file()
    }


def record(venv):
    site = venv / "Lib/site-packages"
    write(WORK / "seed-prepared-files.json", file_map(site))


def stage(venv, payload, python):
    venv, payload, python = map(filesystem_path, (venv, payload, python))
    site = venv / "Lib/site-packages"
    if file_map(site) != read(WORK / "seed-prepared-files.json"):
        raise ValueError("Prepared seed closure changed")
    expected = dict(
        re.findall(
            r"(?m)^([a-zA-Z0-9_.-]+)==([^\s\\]+)",
            (REPO / "scripts/windows/textflowkit-seed-requirements.txt").read_text(),
        )
    )
    packages = {
        re.sub(r"[-_.]+", "-", dist.metadata["Name"]).lower(): dist.version
        for dist in importlib.metadata.distributions(path=[str(site)])
    }
    if packages != expected:
        raise ValueError(f"Installed seed packages differ from the lock: {packages}")
    root = payload / RELATIVE
    if root.exists():
        raise ValueError("Seed staging directory already exists")
    (root / "Lib").mkdir(parents=True)
    shutil.copytree(site, root / "Lib/site-packages")
    # uv metadata for the venv and generated Scripts launchers are not copied.
    # Use the generic interpreter launcher that reads the generated pyvenv.cfg.
    (root / "Scripts").mkdir()
    shutil.copy2(
        python.parent / "Lib/venv/scripts/nt/venvlauncher.exe",
        root / "Scripts/python.exe",
    )
    manifest = {
        "format": "townrecord-runtime-seed-v1",
        "tool": "textflowkit",
        "version": "0.1.19",
        "python_version": "3.13",
        "packages": packages,
        "files": file_map(root),
    }
    write(root / "manifest.json", manifest)
    (payload / ORIGINAL).parent.mkdir(parents=True, exist_ok=True)
    write(payload / ORIGINAL, manifest)
    return {
        "packages": len(packages),
        "files": len(manifest["files"]),
        "manifest_sha256": digest(root / "manifest.json"),
    }


def expected_signed_manifest(payload, overlay):
    original = read(payload / ORIGINAL)
    result = {**original, "files": dict(original["files"])}
    prefix = RELATIVE.as_posix() + "/"
    for item in overlay["files"]:
        if item["kind"] != "payload" or not item["target"].startswith(prefix):
            continue
        relative = item["target"][len(prefix) :]
        if (
            relative not in original["files"]
            or original["files"][relative] != item["unsigned_sha256"]
        ):
            raise ValueError(
                "Signed seed native member differs from original seed manifest"
            )
        result["files"][relative] = item["signed_sha256"]
    return result


def derive(payload, overlay):
    expected = expected_signed_manifest(payload, overlay)
    root = payload / RELATIVE
    actual = file_map(root)
    actual.pop("manifest.json", None)
    if actual != expected["files"]:
        raise ValueError("Signed seed files do not match the derived manifest")
    write(root / "manifest.json", expected)
    return {
        "path": (RELATIVE / "manifest.json").as_posix(),
        "original_sha256": digest(payload / ORIGINAL),
        "derived_sha256": digest(root / "manifest.json"),
        "rule": "original seed metadata with signed PE hashes substituted by verified signing receipt",
    }


def verify_derived(payload, overlay):
    expected = expected_signed_manifest(payload, overlay)
    if read(payload / RELATIVE / "manifest.json") != expected:
        raise ValueError(
            "Derived seed manifest changed beyond its signed native hashes"
        )
    actual = file_map(payload / RELATIVE)
    actual.pop("manifest.json", None)
    if actual != expected["files"]:
        raise ValueError("Derived seed manifest does not cover the signed files")
    return digest(payload / RELATIVE / "manifest.json")


def verify_seed(payload):
    root = payload / RELATIVE
    manifest = read(root / "manifest.json")
    if any(
        manifest.get(key) != value
        for key, value in {
            "format": "townrecord-runtime-seed-v1",
            "tool": "textflowkit",
            "version": "0.1.19",
            "python_version": "3.13",
        }.items()
    ):
        raise ValueError("Seed identity differs from the pinned contract")
    actual = file_map(root)
    actual.pop("manifest.json", None)
    if actual != manifest["files"] or not manifest["packages"]:
        raise ValueError("Seed manifest does not cover its exact files")
    if "Scripts/python.exe" not in actual or any(
        name not in {"Scripts/python.exe", "Scripts/pythonw.exe"}
        and not name.startswith("Lib/site-packages/")
        for name in actual
    ):
        raise ValueError("Seed contains files outside the approved layout")
    return digest(root / "manifest.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["record", "stage"])
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--python", type=Path)
    args = parser.parse_args()
    if args.command == "record":
        record(args.venv)
    else:
        print(json.dumps(stage(args.venv, args.payload, args.python)))
