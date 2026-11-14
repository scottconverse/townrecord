"""Fail packaging when the isolated distribution is incomplete or leaks local data."""

from __future__ import annotations

import json
import re
import runpy
import struct
import sys
from pathlib import Path


def pe_imports(path: Path) -> list[str]:
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise ValueError(f"Expected native PE: {path}")
    pe = struct.unpack_from("<I", data, 60)[0]
    if data[pe : pe + 4] != b"PE\0\0":
        raise ValueError(f"Invalid native PE: {path}")
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    directories = optional + {0x10B: 96, 0x20B: 112}[magic]
    imports = struct.unpack_from("<I", data, directories + 8)[0]
    if not imports:
        return []
    section_count = struct.unpack_from("<H", data, pe + 6)[0]
    section_table = optional + struct.unpack_from("<H", data, pe + 20)[0]

    def offset(rva):
        for index in range(section_count):
            size, address, raw_size, raw = struct.unpack_from(
                "<IIII", data, section_table + index * 40 + 8
            )
            if address <= rva < address + max(size, raw_size):
                return raw + rva - address
        raise ValueError(f"PE import RVA outside sections: {path}")

    cursor = offset(imports)
    names = []
    while any(struct.unpack_from("<IIIII", data, cursor)):
        name = offset(struct.unpack_from("<I", data, cursor + 12)[0])
        names.append(data[name : data.index(b"\0", name)].decode("ascii").lower())
        cursor += 20
    return names


def verify_crt_closure(root: Path) -> None:
    for file in root.rglob("*"):
        if not file.is_file() or file.suffix.lower() not in {".exe", ".dll", ".pyd"}:
            continue
        for name in pe_imports(file):
            if (
                re.fullmatch(
                    r"(?:msvcp140(?:_[a-z0-9_]+)?|vcruntime140(?:_[a-z0-9_]+)?|vcomp140|concrt140|vccorlib140)\.dll",
                    name,
                )
                and not (root / "python" / name).is_file()
            ):
                raise ValueError(
                    f"Missing app-local Microsoft runtime: {name} imported by {file.relative_to(root)}"
                )


def verify(root: Path) -> dict:
    seed = runpy.run_path(
        str(Path(__file__).with_name("seed_payload.py")), run_name="seed_manifest"
    )
    root = seed["filesystem_path"](root)
    for name in (
        "python/tcl",
        "python/Lib/tkinter",
        "python/Lib/turtledemo",
        "python/Lib/turtle.py",
        "python/DLLs/_tkinter.pyd",
        "python/DLLs/tcl86t.dll",
        "python/DLLs/tk86t.dll",
    ):
        if (root / name).exists():
            raise ValueError(f"Unused optional Tcl/Tk component in payload: {name}")
    required = (
        "python/python.exe",
        "python/pythonw.exe",
        "python/LICENSE.txt",
        "python/Lib/venv/__init__.py",
        "python/Lib/ensurepip/__init__.py",
        "python/Lib/site-packages/townrecord/windows.py",
        "python/Lib/site-packages/townrecord/web/community.js",
        "tools/uv.exe",
        "tools/ffmpeg.exe",
        "tools/ffprobe.exe",
        "notices/FFmpeg-LGPL-3.0.txt",
        "notices/uv-LICENSE-MIT.txt",
        "notices/uv-LICENSE-APACHE.txt",
        "notices/uv.lock",
        "LICENSE.txt",
    )
    for name in required:
        if not (root / name).is_file():
            raise ValueError(f"Payload missing required file: {name}")
    pins = json.loads(
        Path(__file__).with_name("dependencies.json").read_text(encoding="utf-8-sig")
    )
    for item in pins["vc-runtime"]["files"]:
        if not (root / "python" / item["name"]).is_file():
            raise ValueError(
                f"Payload missing pinned Microsoft runtime: {item['name']}"
            )
    verify_crt_closure(root)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if path.name.startswith(".env") or path.suffix.lower() in {
            ".db",
            ".sqlite",
            ".sqlite3",
        }:
            raise ValueError(f"User data or environment file in payload: {relative}")
        if set(relative.parts) & {
            ".git",
            "node_modules",
            ".venv",
            "captures",
            "storage",
        }:
            raise ValueError(f"Development/user directory in payload: {relative}")
        if (
            path.suffix.lower() in {".pth", ".json", ".txt"}
            and path.stat().st_size < 1_000_000
        ):
            content = path.read_text(encoding="utf-8", errors="ignore")
            # pip's direct_url record for the app wheel must not retain a local
            # developer path. The installed wheel is identified in the receipt.
            if path.name == "direct_url.json" and "file:///" in content:
                path.unlink()
    if not list((root / "tools").glob("avcodec-*.dll")):
        raise ValueError("FFmpeg shared libraries were not included")
    seed["verify_seed"](root)
    return {"status": "passed", "required_files": len(required)}


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1]).resolve())))
