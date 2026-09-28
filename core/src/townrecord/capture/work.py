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
import shutil
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

#: The folder the audio of one meeting is downloaded into, inside its work
#: folder (spec 8.5). Its own folder, so the audio download's sidecar cannot be
#: read as the caption capture's sidecar: the two runs ask for different things.
AUDIO_DIR_NAME = "audio"

#: The folder one transcription writes its JSON into (spec 8.5). It is a folder
#: of our own, never the one the audio sits in, so a transcript file can be read
#: back without guessing which of its neighbours it is.
TRANSCRIPT_DIR_NAME = "textflowkit"

#: The audio extensions, in the order the download might produce them. The
#: command asks for opus (spec 8.5); the rest are here so a run that converted
#: to something else is still found rather than reported as no audio at all.
AUDIO_EXTENSIONS: tuple[str, ...] = ("opus", "ogg", "m4a", "webm", "mp3", "wav", "aac", "mka")

#: The folder one recheck of a video downloads into (spec 8.7). It is a folder
#: of its own under the video's work folder, and not the work folder itself, for
#: two reasons. The capture's folder is what ``--continue`` resumes into, and a
#: recheck's leftovers there would change what a resumed capture does. And the
#: recheck has to read the source afresh, which the capture's own download
#: archive prevents: the archive records that the video was captured, and yt-dlp
#: skips a video the archive lists. So the recheck keeps its own folder, wipes
#: it before each fetch, and passes an archive inside it that is always empty of
#: this video.
RECHECK_DIR_NAME = "recheck"

#: The download archive a recheck passes. It lives inside the recheck folder,
#: so wiping that folder empties it and the next fetch is never skipped.
RECHECK_ARCHIVE_NAME = "recheck-archive.txt"


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


def audio_dir(storage_root: str | Path, platform_video_id: str) -> Path:
    """Return the folder the audio of a video is downloaded into (spec 8.5)."""
    return work_dir(storage_root, platform_video_id) / AUDIO_DIR_NAME


def transcript_dir(storage_root: str | Path, platform_video_id: str) -> Path:
    """Return the folder a transcription of a video writes its JSON into."""
    return work_dir(storage_root, platform_video_id) / TRANSCRIPT_DIR_NAME


def recheck_dir(storage_root: str | Path, platform_video_id: str) -> Path:
    """Return the folder one recheck of a video downloads into (spec 8.7)."""
    return work_dir(storage_root, platform_video_id) / RECHECK_DIR_NAME


def fresh_recheck_dir(storage_root: str | Path, platform_video_id: str) -> Path:
    """Empty the recheck folder of a video and return it, created.

    Emptying is the point rather than a tidy-up. Each recheck has to read the
    source as it is now, and a file left by the previous check would be read as
    the answer to this one if the fetch wrote nothing.
    """
    folder = recheck_dir(storage_root, platform_video_id)
    if folder.is_dir():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def audio_file(folder: str | Path) -> Path | None:
    """Return the audio file a download wrote, or None (spec 8.5).

    The command asks for opus, and that is what is looked for first; the other
    extensions are looked for after it, so a machine whose converter produced
    something else still yields the file that exists.
    """
    for extension in AUDIO_EXTENSIONS:
        found = files_with_extension(folder, extension)
        if found:
            return found[0]
    return None


def read_info(path: str | Path) -> dict[str, Any]:
    """Read an info.json sidecar. Raises only when the file is not an object."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise ValueError(f"{Path(path).name} does not hold a JSON object.")
    return dict(data)
