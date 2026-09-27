"""The work folder one capture writes into (spec 8.3, 8.7).

"A stopped capture resumes with --continue in the same folder." That sentence
is why the folder is named after the video and kept between runs: a capture
that was killed halfway finds its own partial files and carries on.

The folder name comes from the video id the publisher gave, which is a value
from outside the system, so it is checked against a strict pattern before it is
used to build a path. Nothing here builds a path out of unchecked text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .command import SRV3, VTT

#: The work folder inside the storage root.
WORK_DIR_NAME = "work"

#: A platform video id is a plain token. Anything else is refused, so a value
#: from outside can never steer a path.
FOLDER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: The extensions of the caption file, in the order spec 8.3 names them.
CAPTION_EXTENSIONS: tuple[str, ...] = (SRV3, VTT)

INFO_SUFFIX = ".info.json"


class WorkFolderError(ValueError):
    """A video id cannot be used to build a folder name."""


def folder_name(platform_video_id: str) -> str:
    """Return the work folder name for a video id, or raise."""
    text = (platform_video_id or "").strip()
    if not FOLDER_PATTERN.fullmatch(text):
        raise WorkFolderError(
            f"The video id {text!r} is not a plain token, so TownRecord will not "
            f"build a folder name from it."
        )
    return text


def work_dir(storage_root: str | Path, platform_video_id: str) -> Path:
    """Return the work folder of a video. It is not created here."""
    return Path(storage_root) / WORK_DIR_NAME / folder_name(platform_video_id)


def has_capture(folder: str | Path) -> bool:
    """Return True when the folder already holds files from an earlier run."""
    path = Path(folder)
    if not path.is_dir():
        return False
    return any(entry.is_file() for entry in path.iterdir())


def files_with_extension(folder: str | Path, extension: str) -> list[Path]:
    """Return the files of a folder with one extension, in name order."""
    path = Path(folder)
    if not path.is_dir():
        return []
    return sorted(entry for entry in path.iterdir() if entry.suffix == f".{extension}")


def caption_file(folder: str | Path) -> Path | None:
    """Return the caption file to parse: srv3 first, else vtt (spec 8.3)."""
    for extension in CAPTION_EXTENSIONS:
        found = files_with_extension(folder, extension)
        if found:
            return found[0]
    return None


def info_file(folder: str | Path) -> Path | None:
    """Return the info.json sidecar yt-dlp wrote, or None (spec 8.6)."""
    path = Path(folder)
    if not path.is_dir():
        return None
    found = sorted(entry for entry in path.iterdir() if entry.name.endswith(INFO_SUFFIX))
    return found[0] if found else None


def read_info(path: str | Path) -> dict[str, Any]:
    """Read an info.json sidecar. Raises only when the file is not an object."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"{Path(path).name} does not hold a JSON object.")
    return dict(data)
