"""Fail packaging when the isolated distribution is incomplete or leaks local data."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def verify(root: Path) -> dict:
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
    return {"status": "passed", "required_files": len(required)}


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1]).resolve())))
