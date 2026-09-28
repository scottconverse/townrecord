"""What the service is actually doing (spec 16.3).

Spec 16.3 refuses a health page that answers "OK" from a status code and
nothing else. This module is the other kind: it reads the database, the pointer
file and the settings, and reports counts, names and reasons -- what is queued,
which lane is busy, which source has been failing, what the newest capture of
each body was, which yt-dlp is active, and which of the day's runs could not
happen and why.

Nothing here decides anything and nothing here writes. A fact that is missing
is reported as missing, with the setting that would supply it, because a health
page that quietly rounds "no time zone is set" up to "OK" is the page spec 16.3
exists to forbid.

The subject of the report is the service that is running and not the shell that
asked. A service writes down what it runs with (``serving``), and this reads
that: on 2026-09-27 a service served on 8791 at 17:21 and ``townrecord status``
from another shell printed 8190 and 06:00, each number true of the caller and
false of the service. When no service is running the report says so, and says
that what it shows is this shell's configuration.

The address is part of that subject, and so is who can reach it. A report of a
service on an address beyond this machine warns about it, and a shell whose
configuration would be refused says which setting to turn on rather than warning
about an exposure that cannot happen yet (spec 13.1).

The API's own ``/v1/health`` route stays a small liveness answer: it is a
different question (is this process answering?) and other routes' tests pin its
body. ``townrecord status`` is the content the brief asks for.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from . import network, serving
from .config import Settings
from .db import connect as default_connect
from .jobs import STATES, Clock, JobsSettings, utcnow
from .jobs.queue import stamp
from .repo import (
    BodyCapture,
    JobStateCount,
    all_sources,
    captures_by_body,
    job_kinds,
    job_state_counts,
    latest_runs,
    oldest_queued_at,
    paused_runs,
    running_before,
)
from .repo import counts as missing_counts
from .repo.rows import ScheduledRun, Source
from .runtime import RuntimeManager, RuntimeNotInstalled, RuntimeSettings
from .runtime.pointer import PointerUnreadable
from .runtime.settings import TEXTFLOWKIT_TOOL, TOOL_NAME

#: The tools TownRecord installs for itself (spec 8.9). Both are reported: yt-dlp
#: the capture and update jobs run, and TextFlowKit the transcription job runs.
TOOL_NAMES = (TOOL_NAME, TEXTFLOWKIT_TOOL)

#: How many scheduled runs, and how many captures, the report lists.
DEFAULT_LIMIT = 5

#: A function that opens the database, so a test can hand in its own.
Connect = Callable[[str | Path], Any]


@dataclass(frozen=True)
class JobState:
    """One state of the queue, with the lanes it is spread over."""

    state: str
    count: int
    lanes: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class SourceHealth:
    """One source, and how it has been answering (spec 7.3)."""

    id: int
    type: str
    origin: str
    status: str
    consecutive_failures: int
    last_error: str | None
    last_checked_at: str | None


@dataclass(frozen=True)
class Capture:
    """One body and its newest capture."""

    body_id: int
    body: str
    videos: int
    pending: int
    failed: int
    last_video: str | None
    last_state: str | None
    last_at: str | None


@dataclass(frozen=True)
class Tool:
    """One private tool runtime: which version is active, and whether it works."""

    tool: str
    version: str | None
    previous: str | None
    usable: bool
    reason: str | None


@dataclass(frozen=True)
class Run:
    """One run the daily schedule made, or could not make (spec 16.2)."""

    task: str
    subject: str
    local_date: str
    time_zone: str
    state: str
    job_id: int | None
    reason: str | None


@dataclass(frozen=True)
class Reading:
    """What one pass over the database found, before the settings are added."""

    jobs: tuple[JobState, ...] = ()
    unhandled_kinds: tuple[str, ...] = ()
    running_past_heartbeat: int = 0
    oldest_queued_at: str | None = None
    sources: tuple[SourceHealth, ...] = ()
    captures: tuple[Capture, ...] = ()
    runs: tuple[Run, ...] = ()
    paused: tuple[Run, ...] = ()
    #: How many bodies have a record missing right now (spec 9.5). It is
    #: derived here and read from the meetings, not from the ``missing_records``
    #: rows, because nothing in this module writes and a count of stored rows
    #: would be a count of what some earlier pass happened to find.
    missing_bodies: int = 0


@dataclass(frozen=True)
class Status:
    """Everything the report shows, read from the database and the settings."""

    version: str
    api: str
    host: str
    port: int
    db_path: str
    db_present: bool
    storage_root: str
    runtime_root: str
    time_zone: str
    daily_time: str
    lane_limits: tuple[tuple[str, int], ...]
    kind_limits: tuple[tuple[str, int], ...]
    registered_kinds: tuple[str, ...]
    unhandled_kinds: tuple[str, ...]
    jobs: tuple[JobState, ...]
    running_past_heartbeat: int
    oldest_queued_at: str | None
    sources: tuple[SourceHealth, ...]
    captures: tuple[Capture, ...]
    tools: tuple[Tool, ...]
    runs: tuple[Run, ...]
    paused: tuple[Run, ...]
    #: How many bodies have a record missing right now (spec 9.5). Zero is the
    #: ordinary case, and the report shows the line only when it is not.
    missing_bodies: int = 0
    #: The service that is running on this app-data root, when one is. Everything
    #: above it describes that service when it is here and this shell's settings
    #: when it is not; ``service`` is what tells the two apart.
    service: serving.Served | None = None
    #: True when a service holds the lock but wrote nothing this report can read.
    #: Rare, and reported rather than rounded down to "nothing is running".
    service_locked: bool = False
    #: What a person must be told about the address above (spec 13.1): who can
    #: reach it, or that serving there is not allowed yet. Empty when the address
    #: is this machine alone, which is the default and needs no warning.
    warnings: tuple[str, ...] = field(default=())
    notes: tuple[str, ...] = field(default=())


def collect(
    settings: Settings,
    *,
    clock: Clock = utcnow,
    connect: Connect = default_connect,
    jobs_settings: JobsSettings | None = None,
    runtime_settings: RuntimeSettings | None = None,
    kinds: Sequence[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> Status:
    """Read what the service would show, from the real content of its files.

    A service that is running is the subject of the report, and the settings of
    the shell that asked are not: on 2026-09-27 a service served on 8791 at 17:21
    while ``townrecord status`` from another shell printed 8190 and 06:00, both
    true of the caller and false of the service. So the address, the database,
    the schedule and the time zone come from what the running service wrote down
    whenever there is one to read, and from the settings only when there is not.
    """
    jobs = jobs_settings or JobsSettings()
    runtime = runtime_settings or RuntimeSettings()
    registered = tuple(kinds) if kinds is not None else _registered_kinds()
    notes: list[str] = []
    root = Path(settings.runtime_root)
    locked = serving.is_running(root)
    served = serving.read(root) if locked else None
    host, port = settings.host, settings.port
    time_zone, daily_time = settings.time_zone, settings.daily_time.strftime("%H:%M")
    db_path = Path(settings.db_path)
    #: A service that is running on an address beyond this machine was allowed to
    #: serve there when it started, whatever the setting of the shell that asks
    #: says now: the report is about the service and not about its caller.
    allowed = settings.allow_lan or served is not None
    if served is not None:
        host, port = served.host, served.port
        time_zone, daily_time = served.time_zone, served.daily_time
        db_path = Path(served.db_path)
    present = db_path.is_file()
    reading = Reading()
    if present:
        reading = _read(
            db_path,
            connect,
            clock=clock,
            jobs=jobs,
            registered=registered,
            limit=limit,
            notes=notes,
        )
    else:
        notes.append(
            f"There is no database at {db_path} yet, so there is nothing to report. "
            "The first `townrecord serve` creates it."
        )
    return Status(
        version=settings.version,
        api=f"http://{host}:{port}",
        host=host,
        port=port,
        db_path=str(db_path),
        db_present=present,
        storage_root=str(settings.storage_root),
        runtime_root=str(settings.runtime_root),
        time_zone=time_zone,
        daily_time=daily_time,
        lane_limits=tuple(sorted(jobs.lane_limits.items())),
        kind_limits=tuple(sorted(jobs.kind_limits.items())),
        registered_kinds=registered,
        unhandled_kinds=reading.unhandled_kinds,
        jobs=reading.jobs,
        running_past_heartbeat=reading.running_past_heartbeat,
        oldest_queued_at=reading.oldest_queued_at,
        sources=reading.sources,
        captures=reading.captures,
        tools=_tools(settings, runtime),
        runs=reading.runs,
        paused=reading.paused,
        missing_bodies=reading.missing_bodies,
        service=served,
        service_locked=locked and served is None,
        warnings=_warnings(host, port, allowed),
        notes=tuple(notes),
    )


def _warnings(host: str, port: int, allowed: bool) -> tuple[str, ...]:
    """Say what a person must be told about this address, and nothing else.

    A loopback address is the default and carries no warning. An address the
    network can reach carries the warning about who can reach it once the option
    is on, or the sentence ``serve`` refuses it with while the option is off: the
    two cannot both be true, and neither is invented here (spec 13.1).
    """
    texts = (
        network.lan_warning(host, port, allow_lan=allowed),
        network.lan_refusal(host, allow_lan=allowed),
    )
    return tuple(text for text in texts if text is not None)


def _read(
    db_path: Path,
    connect: Connect,
    *,
    clock: Clock,
    jobs: JobsSettings,
    registered: tuple[str, ...],
    limit: int,
    notes: list[str],
) -> Reading:
    """Read everything that comes from the database, over one connection."""
    conn = connect(db_path)
    try:
        held = tuple(job_kinds(conn))
        stale = stamp(clock() - timedelta(seconds=jobs.heartbeat_timeout))
        return Reading(
            jobs=_jobs(job_state_counts(conn), jobs),
            unhandled_kinds=tuple(kind for kind in held if kind not in registered),
            running_past_heartbeat=running_before(conn, stale),
            oldest_queued_at=oldest_queued_at(conn),
            sources=tuple(_source(row) for row in all_sources(conn)),
            captures=tuple(_capture(row) for row in captures_by_body(conn)),
            runs=tuple(_run(row) for row in latest_runs(conn, limit)),
            paused=tuple(_run(row) for row in paused_runs(conn, limit)),
            missing_bodies=len(missing_counts(conn, now=clock())),
        )
    except sqlite3.Error as exc:
        notes.append(
            f"The database at {db_path} could not be read ({exc}). "
            "It may be a file another program holds, or one from an older version."
        )
        return Reading()
    finally:
        conn.close()


def _jobs(counts: Sequence[JobStateCount], jobs: JobsSettings) -> tuple[JobState, ...]:
    """Group the lane counts by state, in the order a job moves through them."""
    lanes: dict[str, list[tuple[str, int]]] = {}
    for row in counts:
        lanes.setdefault(row.state, []).append((row.lane, row.count))
    ordered = list(STATES) + sorted(state for state in lanes if state not in STATES)
    return tuple(
        JobState(
            state=state,
            count=sum(count for _lane, count in lanes[state]),
            lanes=tuple(sorted(lanes[state])),
        )
        for state in ordered
        if state in lanes
    )


def _source(row: Source) -> SourceHealth:
    return SourceHealth(
        id=row.id,
        type=row.type,
        origin=row.origin,
        status=row.status,
        consecutive_failures=row.consecutive_failures,
        last_error=row.last_error,
        last_checked_at=row.last_checked_at,
    )


def _capture(row: BodyCapture) -> Capture:
    return Capture(
        body_id=row.body_id,
        body=row.body,
        videos=row.videos,
        pending=row.pending,
        failed=row.failed,
        last_video=row.last_video,
        last_state=row.last_state,
        last_at=row.last_at,
    )


def _run(row: ScheduledRun) -> Run:
    return Run(
        task=row.task,
        subject=row.subject,
        local_date=row.local_date,
        time_zone=row.time_zone,
        state=row.state,
        job_id=row.job_id,
        reason=row.reason,
    )


def _tools(settings: Settings, runtime: RuntimeSettings) -> tuple[Tool, ...]:
    """Report each private runtime: the version active now, and whether it works."""
    return tuple(_tool(settings, runtime, tool) for tool in TOOL_NAMES)


def _tool(settings: Settings, runtime: RuntimeSettings, tool: str) -> Tool:
    manager = RuntimeManager(root=settings.runtime_root, settings=runtime, tool=tool)
    try:
        pointer = manager.read_pointer()
    except PointerUnreadable as exc:
        return Tool(tool=tool, version=None, previous=None, usable=False, reason=str(exc))
    active = pointer.active
    try:
        if tool == TOOL_NAME:
            manager.interpreter()
        else:
            manager.program()
    except (RuntimeNotInstalled, ValueError, RuntimeError) as exc:
        return Tool(
            tool=tool,
            version=None if active is None else active.version,
            previous=None if pointer.previous is None else pointer.previous.version,
            usable=False,
            reason=str(exc),
        )
    return Tool(
        tool=tool,
        version=None if active is None else active.version,
        previous=None if pointer.previous is None else pointer.previous.version,
        usable=True,
        reason=None,
    )


def _registered_kinds() -> tuple[str, ...]:
    """Return the job kinds the service wires. Imported here to avoid a cycle."""
    from .service import kinds as service_kinds

    return service_kinds()


def render(status: Status) -> str:
    """Write the report the way ``townrecord status`` prints it."""
    lines = [f"TownRecord {status.version}"]
    lines.append(f"  Service    {_service_line(status)}")
    lines.append(f"  API        {status.api} (a token is required on every route)")
    lines.append(f"  Database   {_database_line(status)}")
    lines.append(f"  Storage    {status.storage_root}")
    lines.append(f"  Runtimes   {status.runtime_root}")
    lines.append(f"  Schedule   {_schedule_line(status)}")
    if status.missing_bodies:
        lines.append(f"  Missing    {_missing_line(status)}")
    lines.extend(status.warnings)
    lines.append("")
    lines.extend(_job_lines(status))
    lines.append("")
    lines.extend(_source_lines(status))
    lines.append("")
    lines.extend(_capture_lines(status))
    lines.append("")
    lines.extend(_tool_lines(status))
    lines.append("")
    lines.extend(_run_lines(status))
    for note in status.notes:
        lines.append("")
        lines.append(f"Note: {note}")
    return "\n".join(lines)


def _service_line(status: Status) -> str:
    """Say which service the lines below describe, or that none is running.

    The process id is printed for a person to read and is never acted on: on
    Windows, asking whether a process id is alive is a call that ends it.
    """
    if status.service is not None:
        return (
            f"running as process {status.service.pid} at {status.service.api} since "
            f"{status.service.started_at} (the values below are the ones it runs with)"
        )
    if status.service_locked:
        return (
            f"a service holds the lock on {status.runtime_root} but wrote nothing this "
            "report can read, so the values below are this shell's configuration, not a "
            "running server"
        )
    return (
        "no service is running, so the address, the schedule and the time zone below are "
        "this shell's configuration, not a running server"
    )


def _database_line(status: Status) -> str:
    if not status.db_present:
        return f"{status.db_path} (not created yet)"
    return status.db_path


def _schedule_line(status: Status) -> str:
    if not status.time_zone.strip():
        return (
            "no time zone is set, so the daily runs have no local time to run at "
            "(set TOWNRECORD_TIME_ZONE)"
        )
    return f"{status.time_zone}, daily at {status.daily_time} local time"


def _missing_line(status: Status) -> str:
    """Say how many bodies are missing a record, in one line (spec 9.5).

    The line is shown only when the count is not zero, because the ordinary
    case is that nothing is missing and a report that repeated "0" every time
    would train a reader to skip the line that matters. The words say which
    rule was applied, so the count is not a number with no definition.
    """
    return (
        f"{status.missing_bodies} body(ies) have a record missing 36 hours or more "
        "after a meeting that was not cancelled or continued"
    )


def _job_lines(status: Status) -> list[str]:
    limits = ", ".join(f"{lane} {count}" for lane, count in status.lane_limits)
    kinds = ", ".join(f"{kind} {count}" for kind, count in status.kind_limits)
    lines = [f"Jobs ({limits} at a time; {kinds})"]
    if not status.jobs:
        lines.append("  nothing has been queued yet")
    for job in status.jobs:
        lanes = ", ".join(f"{lane} {count}" for lane, count in job.lanes)
        lines.append(f"  {job.state:<8} {job.count:>4}  ({lanes})")
    if status.running_past_heartbeat:
        lines.append(
            f"  {status.running_past_heartbeat} running job(s) have stopped sending heartbeats "
            "and will be put back in the queue"
        )
    if status.oldest_queued_at:
        lines.append(f"  oldest queued job has waited since {status.oldest_queued_at}")
    if status.unhandled_kinds:
        lines.append(
            "  no handler is registered for: "
            + ", ".join(status.unhandled_kinds)
            + " -- jobs of those kinds cannot run"
        )
    return lines


def _source_lines(status: Status) -> list[str]:
    lines = ["Sources"]
    if not status.sources:
        lines.append("  none yet")
    for source in status.sources:
        parts = [f"  {source.id}  {source.type:<16} {source.status:<9} {source.origin}"]
        if source.consecutive_failures:
            parts.append(f"      {source.consecutive_failures} failure(s) in a row")
        if source.last_error:
            parts.append(f"      last error: {source.last_error}")
        if source.last_checked_at:
            parts.append(f"      last checked {source.last_checked_at}")
        lines.append("\n".join(parts))
    return lines


def _capture_lines(status: Status) -> list[str]:
    lines = ["Captures (newest video of each body)"]
    if not status.captures:
        lines.append("  no body has been recorded yet")
    for capture in status.captures:
        if capture.last_video is None:
            lines.append(f"  {capture.body}: no videos yet")
            continue
        lines.append(
            f"  {capture.body}: {capture.videos} video(s), {capture.pending} pending, "
            f"{capture.failed} without captions; newest {capture.last_video} "
            f"({capture.last_state}) at {capture.last_at}"
        )
    return lines


def _tool_lines(status: Status) -> list[str]:
    lines = ["Tools"]
    for tool in status.tools:
        if tool.usable:
            previous = f", previous {tool.previous}" if tool.previous else ""
            lines.append(f"  {tool.tool}: {tool.version} active{previous}")
        elif tool.version:
            lines.append(f"  {tool.tool}: {tool.version} installed but not usable -- {tool.reason}")
        else:
            lines.append(f"  {tool.tool}: not installed -- {tool.reason}")
    return lines


def _run_lines(status: Status) -> list[str]:
    lines = ["Scheduled runs (newest first)"]
    if not status.runs:
        lines.append("  the schedule has not run yet")
    for run in status.runs:
        lines.append(f"  {_run_line(run)}")
    shown = {(run.task, run.subject, run.local_date) for run in status.runs}
    extra = [run for run in status.paused if (run.task, run.subject, run.local_date) not in shown]
    if extra:
        lines.append("Runs that could not happen")
        lines.extend(f"  {_run_line(run)}" for run in extra)
    return lines


def _run_line(run: Run) -> str:
    job = "" if run.job_id is None else f" (job {run.job_id})"
    why = "" if not run.reason else f" -- {run.reason}"
    return (
        f"{run.local_date}  {run.task:<15} {run.subject:<18} {run.state}{job} "
        f"[{run.time_zone}]{why}"
    )


__all__ = [
    "DEFAULT_LIMIT",
    "TOOL_NAMES",
    "Capture",
    "Connect",
    "JobState",
    "Reading",
    "Run",
    "SourceHealth",
    "Status",
    "Tool",
    "collect",
    "render",
]
