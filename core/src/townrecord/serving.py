"""Where a running service says what it runs with, and how another shell reads it.

Spec 16.3 refuses a health page that answers "OK" from a status code. The same
refusal applies to a person reading a report. On 2026-09-27 a live run served on
port 8791 with its daily time at 17:21, and ``townrecord status`` in another
shell printed port 8190 and 06:00. Both numbers were true -- of the *settings of
the shell that asked*. The report was describing the configuration of its caller
as though it were the state of the running service, which is the same quiet
rounding spec 16.3 exists to forbid.

So a service that starts writes down what it actually runs with, and the report
reads that:

* the host, the port, the daily time and the time zone, which is where the live
  run and the report disagreed;
* the process id and the moment the service started, so a person can see which
  run this is;
* the database it opened.

**The process id is shown and never acted on.** Whether a service is running is
answered by the lock below and never by a guess about a number: on Windows the
way to ask whether a pid is alive is ``os.kill(pid, 0)``, and that call *ends*
the process it is asked about. A pid read out of a file is therefore never
probed.

The two files live under the app-data root (``runtime_root``, spec 8.6), beside
the database and the pace file:

``service.lock``
    Held by the running service for as long as it runs (``msvcrt.locking`` on
    Windows, ``flock`` elsewhere). The operating system owns the lock, so a
    process that dies hard -- a kill, a power cut -- releases it: a crashed
    service reads as not running, which is the fact.
``service.json``
    What the service runs with, written atomically (spec 8.6): a temporary file
    beside it, flushed and ``fsync``ed, then ``os.replace``d over the old one. A
    file with no lock behind it is a leftover from a service that is gone and is
    reported as nothing at all.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import time
from pathlib import Path
from typing import IO, Any

from .jobs import Clock, utcnow
from .jobs.queue import stamp

logger = logging.getLogger(__name__)

#: The version of the service file, so a later change to it is a visible one.
#: It is the version of the file itself and not of TownRecord.
SERVICE_VERSION = 1

#: What the running service wrote down about itself.
SERVICE_FILE_NAME = "service.json"

#: The lock the running service holds. Its presence is not the fact; the
#: operating system lock on it is.
SERVICE_LOCK_NAME = "service.lock"


@dataclass(frozen=True)
class Served:
    """One running service, as it wrote itself down."""

    version: int
    pid: int
    host: str
    port: int
    daily_time: str
    time_zone: str
    db_path: str
    started_at: str

    @property
    def api(self) -> str:
        """The address the running service answers on."""
        return f"http://{self.host}:{self.port}"


@dataclass
class ServiceLock:
    """This process's claim on one app-data root, held by the operating system.

    It is deliberately not a check on a process id: the lock is the fact, and a
    process that dies hard leaves no lock behind.
    """

    root: Path
    path: Path
    #: True when this process holds the lock, false when another one does.
    held: bool = False
    #: True when the lock file could be opened at all. False means this root
    #: cannot carry a service file, and nothing is reported as running.
    opened: bool = False
    #: The open lock file. It is kept here so the lock stays held; closing it
    #: would give the lock back.
    handle: IO[bytes] | None = None

    @classmethod
    def acquire(cls, root: str | Path, *, create: bool = True) -> ServiceLock:
        """Take the lock for ``root``, or return a lock somebody else holds.

        ``create`` False never makes the folder, so a caller that is only asking
        whether a service is running leaves the disk as it found it. A root that
        is not there holds no service and there is nothing to find out by making
        one. A service taking the lock does make it: it is about to write there.
        """
        folder = Path(root)
        path = folder / SERVICE_LOCK_NAME
        if not create and not folder.is_dir():
            return cls(root=folder, path=path)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            handle = _open_lock_file(path)
        except OSError as exc:
            logger.warning("The service lock %s could not be opened: %s", path, exc)
            return cls(root=folder, path=path)
        if not _take(handle):
            _close(handle)
            return cls(root=folder, path=path, opened=True)
        return cls(root=folder, path=path, held=True, opened=True, handle=handle)

    def release(self) -> None:
        """Give the lock back. Safe to call twice, and on a lock never taken."""
        if self.handle is None:
            return
        _let_go(self.handle)
        _close(self.handle)
        self.handle = None
        self.held = False


def _open_lock_file(path: Path) -> IO[bytes]:
    """Open the lock file, making it when it is not there, and never truncating it.

    Truncating would be a write to a region another process may be holding, so
    the file is only ever read and extended. One byte is enough to lock: the
    operating system locks a range of a file, not the file itself.
    """
    try:
        handle = open(path, "r+b")  # noqa: SIM115 - the open handle is the lock itself
    except FileNotFoundError:
        handle = open(path, "w+b")  # noqa: SIM115 - the open handle is the lock itself
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    return handle


def _take(handle: IO[bytes]) -> bool:
    """Lock the first byte, or False when another process holds it."""
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _let_go(handle: IO[bytes]) -> None:
    """Unlock the first byte, and say so rather than raise if it cannot."""
    with contextlib.suppress(OSError, ValueError):
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _close(handle: IO[bytes]) -> None:
    with contextlib.suppress(OSError):
        handle.close()


def is_running(root: str | Path) -> bool:
    """True when some process holds the service lock on this app-data root.

    A root that cannot be opened at all is not running: there is nothing to
    report, and saying "a service is running" about a folder nobody can write
    would be a guess. Asking the question creates nothing: ``townrecord status``
    reads, and a folder that is not there holds no service.
    """
    claim = ServiceLock.acquire(root, create=False)
    if claim.held:
        claim.release()
        return False
    return claim.opened


def read(root: str | Path) -> Served | None:
    """Return what a service wrote down, or None when there is nothing readable."""
    path = Path(root) / SERVICE_FILE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, Mapping):
        return None
    try:
        return Served(
            version=int(data["version"]),
            pid=int(data["pid"]),
            host=str(data["host"]),
            port=int(data["port"]),
            daily_time=str(data["daily_time"]),
            time_zone=str(data["time_zone"]),
            db_path=str(data["db_path"]),
            started_at=str(data["started_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("The service file %s could not be read (%s), so it is ignored.", path, exc)
        return None


def record(
    root: str | Path,
    *,
    host: str,
    port: int,
    daily_time: time,
    time_zone: str,
    db_path: str,
    pid: int | None = None,
    clock: Clock = utcnow,
) -> ServiceLock:
    """Take the lock for this app-data root and write down what is being served.

    ``pid`` defaults to this process's own: the number written is the service's
    own process id and never one a caller guessed. It is written for a person to
    read, and nothing in TownRecord ever acts on it.

    A service that cannot take the lock writes nothing. Another service already
    says it is serving from this root, and a second file would take that claim
    away from it; the warning says so, and ``townrecord status`` keeps reporting
    the service that holds the lock.
    """
    claim = ServiceLock.acquire(root)
    if not claim.held:
        logger.warning(
            "Another TownRecord holds the service lock on %s, so this one does not write %s.",
            claim.root,
            SERVICE_FILE_NAME,
        )
        return claim
    served = Served(
        version=SERVICE_VERSION,
        pid=os.getpid() if pid is None else pid,
        host=host,
        port=port,
        daily_time=daily_time.strftime("%H:%M"),
        time_zone=time_zone,
        db_path=str(db_path),
        started_at=stamp(clock()),
    )
    _write_json(claim.root / SERVICE_FILE_NAME, asdict(served))
    return claim


def clear(claim: ServiceLock) -> None:
    """Take away what the service wrote and give the lock back.

    A process that never held the lock takes away nothing: it wrote no file, and
    the one on disk belongs to the service that did.
    """
    if claim.held:
        with contextlib.suppress(OSError):
            (claim.root / SERVICE_FILE_NAME).unlink()
    claim.release()


def _write_json(path: Path, value: Any) -> None:
    """Write JSON where a reader sees the whole file or none of it (spec 8.6).

    A temporary file in the same folder, flushed and ``fsync``ed, then moved over
    the target. A reader of the target therefore never sees half a file, and a
    process that dies mid-write leaves the old one in place.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


__all__ = [
    "SERVICE_FILE_NAME",
    "SERVICE_LOCK_NAME",
    "SERVICE_VERSION",
    "ServiceLock",
    "Served",
    "clear",
    "is_running",
    "read",
    "record",
]
