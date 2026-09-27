"""One update at a time (spec 8.9).

The daily check installs a new yt-dlp beside the current one, tests it, and
switches the pointer. Two of those running at once would race on the same
folder and on the pointer file, so each run takes a lock file next to the
runtimes, and the second one is refused with a plain reason instead of being
allowed to interleave. A jobs-lane rule is not enough here: the lock has to
hold across processes, and the scheduler of spec 16.2 may enqueue the update
from a different run of the service.

The lock file holds the process id, the time it was taken and what it is for,
so a person who finds it can see what is going on. A lock whose file is older
than `stale_after_s` is read as left behind by a crash (a killed update would
otherwise block every later update forever), and it is taken over.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .settings import LOCK_NAME, TOOL_NAME


class UpdateInProgress(RuntimeError):
    """Another update is running, so this one was not started."""


@dataclass
class UpdateLock:
    """The lock that lets one update run at a time.

    Used as a context manager: :meth:`acquire` raises
    :class:`UpdateInProgress` when the lock is held, and the file is removed
    when the block ends.

    There is one lock per tool, because the folder it guards is that tool's
    (``<runtimes>/<tool>``). The tool's name is read from that folder unless it
    is given, so the file and the sentences name the tool they are about.
    """

    tool_root: Path
    stale_after_s: float = 3600.0
    #: The clock the staleness rule reads. Tests move it by hand.
    now: Callable[[], float] = field(default=time.time, repr=False)
    #: What the lock is for, kept in the file for the person who finds it.
    #: Empty means ``<tool> update``.
    purpose: str = ""
    #: The tool this lock guards. Empty means the folder's own name.
    tool: str = ""
    _taken: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.tool.strip():
            self.tool = Path(self.tool_root).name or TOOL_NAME
        if not self.purpose.strip():
            self.purpose = f"{self.tool} update"

    @property
    def path(self) -> Path:
        """Return the lock file's path."""
        return Path(self.tool_root) / LOCK_NAME

    def acquire(self) -> None:
        """Take the lock, or raise UpdateInProgress when it is already held."""
        folder = Path(self.tool_root)
        folder.mkdir(parents=True, exist_ok=True)
        path = self.path
        for attempt in (1, 2):
            try:
                handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                stale = self._stale_reason()
                if attempt == 2 or stale is None:
                    raise UpdateInProgress(
                        stale
                        or f"Another {self.tool} update is running, so this one did not start."
                    ) from None
                # The holder is gone: take the file over and try once more.
                with contextlib.suppress(OSError):
                    path.unlink()
                continue
            except OSError as exc:
                raise UpdateInProgress(
                    f"The update lock {path} could not be created: {exc}"
                ) from exc
            else:
                taken_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now()))
                with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
                    json.dump(
                        {"pid": os.getpid(), "taken_at": taken_at, "purpose": self.purpose},
                        file,
                    )
                    file.write("\n")
                self._taken = True
                return
        raise UpdateInProgress(  # pragma: no cover - the loop either takes it or raises
            f"Another {self.tool} update is running, so this one did not start."
        )

    def _stale_reason(self) -> str | None:
        """Return a plain reason when the lock file was left by a crash."""
        try:
            age = self.now() - self.path.stat().st_mtime
        except OSError:
            return None
        if age <= max(0.0, self.stale_after_s):
            return None
        return (
            f"A {self.tool} update started {age / 3600:.1f} hours ago and never finished, "
            "so its lock was taken over."
        )

    def release(self) -> None:
        """Remove the lock file, when this run is the one that holds it."""
        if not self._taken:
            return
        with contextlib.suppress(OSError):  # already gone is fine
            self.path.unlink()
        self._taken = False

    def __enter__(self) -> UpdateLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
