"""The one place TownRecord is wired together (spec 14.2, 16.1, 16.2).

Nothing starts TownRecord today except this module. ``townrecord serve`` builds
one :class:`Service` and that service is the core the desktop shell starts and
stops: the API is started from the settings, the job workers of spec 16.1 run
in the same process, and the daily schedule of spec 16.2 runs beside them.

One registry holds **every job kind that exists**, with the storage root, the
runtime root and the handlers' programs coming from the settings and from
nowhere else (spec 8.6, 8.9). A kind that is missing from this list is a job
nothing can run: a job of that kind would sit in the queue until its worker
found no handler for it and failed it. :data:`KIND_MODULES` is that list, and
the test of this unit checks that it matches what the registry actually holds.

The handlers are built with their real programs -- the interpreter of the
private runtime of spec 8.9 when one is installed, this process's interpreter
when none is -- and they follow the runtime: one the first run installs while
the service is up is used by the next job, with no restart. They get an
``httpx`` client for the one PyPI call. Nothing
here reaches the network: a client is built, and no request is made until a
job runs.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from . import pacing, records
from .capture import JOB_KIND as CAPTURE_JOB_KIND
from .capture import RECHECK_JOB_KIND, TRANSCRIBE_JOB_KIND, register_recheck, register_transcribe
from .capture import register as register_capture
from .capture.command import Runner as CaptureRunner
from .capture.probe import capture_probe
from .config import Settings
from .db import connect as default_connect
from .jobs import Clock, JobsSettings, Registry, Runner, utcnow
from .records import (
    ALIGN_MEETING,
    DOWNLOAD_RECORD,
    EXTRACT_PAGES,
    READ_MINUTES,
    READ_SPEAKERS,
    SYNC_PRIMEGOV,
)
from .runtime import JOB_KIND as RUNTIME_UPDATE_KIND
from .runtime import (
    RuntimeManager,
    RuntimeNotInstalled,
    RuntimeSettings,
    program_probe,
    register_tools,
)
from .runtime.settings import TEXTFLOWKIT_PROGRAM, TEXTFLOWKIT_TOOL, TOOL_NAME
from .schedule import Scheduler
from .video.watch import JOB_KIND as WATCH_CHANNEL_JOB_KIND
from .video.watch import LANE as WATCH_CHANNEL_LANE
from .video.watch import register as register_watch_channel
from .video.watch import service_ladder

logger = logging.getLogger(__name__)

#: Every job kind that exists, with the module that registers it (spec 16.1).
#: The order is the order they are wired in. This is the list the service
#: registers and the list ``townrecord status`` counts against.
KIND_MODULES: tuple[tuple[str, str], ...] = (
    (SYNC_PRIMEGOV, "townrecord.records"),
    (DOWNLOAD_RECORD, "townrecord.records"),
    (EXTRACT_PAGES, "townrecord.records"),
    (ALIGN_MEETING, "townrecord.records"),
    (READ_MINUTES, "townrecord.records"),
    (READ_SPEAKERS, "townrecord.records"),
    (CAPTURE_JOB_KIND, "townrecord.capture"),
    (TRANSCRIBE_JOB_KIND, "townrecord.capture"),
    (RECHECK_JOB_KIND, "townrecord.capture"),
    (WATCH_CHANNEL_JOB_KIND, "townrecord.video.watch"),
    (RUNTIME_UPDATE_KIND, "townrecord.runtime"),
)

#: How long the one PyPI call of spec 8.9 may take.
DEFAULT_HTTP_TIMEOUT_S = 30.0

#: A function that opens the database, so a test can hand in its own.
Connect = Callable[[str | Path], Any]


def kinds() -> tuple[str, ...]:
    """Return every job kind that exists."""
    return tuple(kind for kind, _module in KIND_MODULES)


@dataclass
class Service:
    """One running core: a registry, its workers, its schedule, its API.

    ``start`` starts the workers and the schedule and returns; the caller owns
    the API process (``townrecord serve`` runs uvicorn around it) and calls
    ``stop`` when the user stops the service. ``stop`` is the clean end of
    spec 16.1: workers stop claiming, and a job that was running keeps its
    checkpoint and is claimable again.

    It is not frozen: ``close`` forgets the client it closed, which is what
    makes closing twice the same as closing once.
    """

    settings: Settings
    registry: Registry
    runner: Runner
    scheduler: Scheduler
    handlers: Mapping[str, object] = field(default_factory=dict)
    #: The one HTTP client the runtime update job uses. It is closed with the
    #: service so the process does not leave a connection pool behind.
    client: httpx.Client | None = None
    #: The one pace every YouTube request of this process waits on (spec 8.10).
    #: It is installed process-wide by ``build_service`` and taken away again
    #: by ``close``, so a process that ran a service does not keep its state
    #: file open behind the next one.
    pacer: pacing.Pacer | None = None

    def start(self) -> None:
        """Start the job workers and the daily schedule."""
        self.runner.start()
        self.scheduler.start()
        logger.info(
            "TownRecord is serving %s jobs on %s and scheduling the day from %s.",
            len(self.registry.kinds()),
            self.settings.db_path,
            self.settings.time_zone or "an unset time zone",
        )

    def stop(self, timeout: float = 5.0) -> bool:
        """Stop the schedule and the workers. True when both stopped in time."""
        scheduled = self.scheduler.stop(timeout)
        workers = self.runner.stop(timeout)
        return scheduled and workers

    def close(self) -> None:
        """Close what the service opened. Safe to call twice."""
        if self.client is not None:
            self.client.close()
            self.client = None
        # The process pace is the service's, so the service gives it back. A
        # pace somebody else installed is left alone (:func:`pacing.forget_pacer`).
        if pacing.forget_pacer(self.pacer):
            self.pacer = None


def build_service(
    settings: Settings,
    *,
    clock: Clock = utcnow,
    client: httpx.Client | None = None,
    probe: Callable[[str], bool] | None = None,
    capture_runner: CaptureRunner | None = None,
    jobs_settings: JobsSettings | None = None,
    runtime_settings: RuntimeSettings | None = None,
    connect: Connect = default_connect,
) -> Service:
    """Build the core from the settings, with every job kind wired.

    The arguments that are not settings exist for the tests: a fake clock moves
    the schedule through a daylight saving change, a fake probe tests an update
    without a video, and a mock client answers the PyPI call. A real run passes
    none of them, and the real probe is the known-video test of spec 8.9.
    """
    registry = Registry()
    records.register_jobs(registry)
    runtime = runtime_settings or RuntimeSettings()
    manager = RuntimeManager(root=settings.runtime_root, settings=runtime, tool=TOOL_NAME)
    # Said once at start, so the log shows what the first jobs will find.
    _interpreter_or_none(manager)
    _textflowkit_or_none(settings, runtime)
    real_client = _client_or_new(client)
    handlers = _register_handlers(
        settings,
        registry,
        client=real_client,
        probe=probe if probe is not None else _default_probe(settings, capture_runner),
        capture_runner=capture_runner,
        runtime_settings=runtime,
    )
    runner = Runner(
        settings.db_path,
        registry=registry,
        settings=jobs_settings or JobsSettings(),
        clock=clock,
        connect=connect,
    )
    scheduler = Scheduler(
        db_path=settings.db_path,
        registry=registry,
        time_zone=settings.time_zone,
        daily_time=settings.daily_time,
        test_video_url=settings.test_video_url,
        clock=clock,
        connect=connect,
    )
    logger.info(
        "TownRecord wired %s job kinds: %s.", len(registry.kinds()), ", ".join(registry.kinds())
    )
    pace = _install_pacer(settings)
    return Service(
        settings=settings,
        registry=registry,
        runner=runner,
        scheduler=scheduler,
        handlers=handlers,
        client=real_client,
        pacer=pace,
    )


def _install_pacer(settings: Settings) -> pacing.Pacer:
    """Build the one pace for this process and install it (spec 8.10).

    Every handler asks :func:`townrecord.pacing.pacer` for the pace rather than
    being handed one, so installing it here is what makes one service mean one
    pace: a capture, a status ask, a feed read and an audio download all wait
    on the same next-allowed time, whatever lane or worker they run on.

    The state file lives beside the runtime pointer under the app-data root
    (spec 8.6), so a restart reads the hold a 429 left behind instead of
    walking straight back into it. The two numbers come from the environment
    (:meth:`townrecord.pacing.PaceSettings.from_env`).

    The time zone is the user's own, because the sentence a deferred job
    carries names the moment a hold ends, and a person reads the clock on the
    wall rather than UTC (spec 16.3).
    """
    pace = pacing.Pacer(
        settings=pacing.PaceSettings.from_env(),
        state_path=settings.runtime_root / pacing.PACE_FILE_NAME,
        time_zone=settings.time_zone,
    )
    pacing.use_pacer(pace)
    logger.info(
        "YouTube requests are paced %.1fs apart, with a %.0fs hold after HTTP 429 (%s).",
        pace.settings.minimum_gap_s,
        pace.settings.rate_limit_backoff_s,
        pace.state_path,
    )
    return pace


#: The programs a handler runs: the yt-dlp interpreter and the TextFlowKit
#: program, each None while its runtime is not installed.
Programs = tuple[str | None, str | None]

#: Builds one handler, registering it in the registry it is given.
Builder = Callable[[Registry, str | None, str | None], Callable[[Any], None]]


@dataclass
class FollowsRuntime:
    """A handler that is rebuilt when the active runtime changes (spec 8.9).

    The first run installs yt-dlp and TextFlowKit with ``runtime_update`` jobs
    while the service is already up, and an update can switch a pointer to a
    newer version on any later day. A handler built once at start keeps the
    programs it was built with -- this process's interpreter, which cannot
    import yt-dlp -- until a restart. So each job asks which programs the
    pointers name now, and the inner handler is rebuilt when they changed.
    """

    build: Callable[[str | None, str | None], Callable[[Any], None]]
    resolve: Callable[[], Programs]
    _programs: Programs | None = None
    _handler: Callable[[Any], None] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def current(self) -> Any:
        """The inner handler for the runtime that is active now."""
        programs = self.resolve()
        with self._lock:
            if self._handler is None or programs != self._programs:
                self._handler = self.build(*programs)
                self._programs = programs
            return self._handler

    def __call__(self, ctx: Any) -> None:
        self.current()(ctx)


#: How long a job that needs a runtime waits before it asks again. The first
#: install takes about ten seconds, so a minute is not a long wait.
RUNTIME_WAIT_S = 60.0


@dataclass(frozen=True)
class WaitsForRuntime:
    """The handler for a job whose program is not installed yet (spec 8.9).

    A watch's last listing step is yt-dlp, and before the first
    ``runtime_update`` finishes there is no yt-dlp to run: this process's
    interpreter cannot import it. The job goes back in the queue with a plain
    reason, and :class:`FollowsRuntime` builds the real handler once the
    pointer names an install.
    """

    tool: str
    delay_s: float = RUNTIME_WAIT_S

    def __call__(self, ctx: Any) -> None:
        ctx.defer(
            f"The {self.tool} runtime is not installed yet, so this job waits for "
            "the runtime update to finish.",
            delay_s=self.delay_s,
        )


def _follow(
    registry: Registry, kind: str, build: Builder, resolve: Callable[[], Programs]
) -> FollowsRuntime:
    """Register `kind` with a handler that follows the active runtime.

    `build` is the module's own ``register``, run against a registry nobody
    reads, so the inner handler is built exactly as before and only the wrapper
    goes into the real registry, in the lane the module chose.
    """
    scratch = Registry()
    first = build(scratch, *resolve())
    registration = scratch.registration(kind)
    if registration is None:
        raise RuntimeError(f"The builder for {kind} registered nothing.")
    wrapper = FollowsRuntime(build=lambda py, tfk: build(Registry(), py, tfk), resolve=resolve)
    wrapper._handler, wrapper._programs = first, resolve()
    registry.register(kind, wrapper, lane=registration.lane)
    return wrapper


def _register_handlers(
    settings: Settings,
    registry: Registry,
    *,
    client: httpx.Client,
    probe: Callable[[str], bool],
    capture_runner: CaptureRunner | None,
    runtime_settings: RuntimeSettings,
) -> dict[str, object]:
    """Wire each handler with the paths and programs it runs."""
    ytdlp = RuntimeManager(root=settings.runtime_root, settings=runtime_settings, tool=TOOL_NAME)
    storage = settings.storage_root

    def resolve() -> Programs:
        return (
            _interpreter_or_none(ytdlp, quiet=True),
            _textflowkit_or_none(settings, runtime_settings, quiet=True),
        )

    def capture(reg: Registry, py: str | None, _tfk: str | None) -> Callable[[Any], None]:
        return register_capture(
            storage_root=storage, registry=reg, runner=capture_runner, interpreter=py
        )

    def transcribe(reg: Registry, py: str | None, tfk: str | None) -> Callable[[Any], None]:
        return register_transcribe(
            storage_root=storage,
            registry=reg,
            runner=capture_runner,
            interpreter=py,
            textflowkit_program=tfk,
        )

    def recheck(reg: Registry, py: str | None, _tfk: str | None) -> Callable[[Any], None]:
        return register_recheck(
            storage_root=storage, registry=reg, runner=capture_runner, interpreter=py
        )

    def watch(reg: Registry, py: str | None, _tfk: str | None) -> Callable[[Any], None]:
        if py is None:
            waiting = WaitsForRuntime(tool=TOOL_NAME)
            reg.register(WATCH_CHANNEL_JOB_KIND, waiting, lane=WATCH_CHANNEL_LANE)
            return waiting
        return register_watch_channel(
            registry=reg, ladder=service_ladder(client=client, interpreter=py)
        )

    handlers: dict[str, object] = {
        CAPTURE_JOB_KIND: _follow(registry, CAPTURE_JOB_KIND, capture, resolve),
        TRANSCRIBE_JOB_KIND: _follow(registry, TRANSCRIBE_JOB_KIND, transcribe, resolve),
        RECHECK_JOB_KIND: _follow(registry, RECHECK_JOB_KIND, recheck, resolve),
        WATCH_CHANNEL_JOB_KIND: _follow(registry, WATCH_CHANNEL_JOB_KIND, watch, resolve),
        # One kind, and the payload's tool says which runtime a run updates.
        # TextFlowKit has no video to test against: its test is its program.
        RUNTIME_UPDATE_KIND: register_tools(
            root=settings.runtime_root,
            registry=registry,
            client=client,
            probes={TOOL_NAME: probe, TEXTFLOWKIT_TOOL: program_probe(TEXTFLOWKIT_PROGRAM)},
            test_video_urls={
                TOOL_NAME: settings.test_video_url,
                TEXTFLOWKIT_TOOL: f"its own program ({TEXTFLOWKIT_PROGRAM} --version)",
            },
            settings=runtime_settings,
        ),
    }
    for kind, _handler, _lane in records.jobs:
        registration = registry.registration(kind)
        handlers[kind] = None if registration is None else registration.handler
    return handlers


def _client_or_new(client: httpx.Client | None) -> httpx.Client:
    """Return the caller's client, or a real one for the PyPI call.

    ``trust_env=False`` is the same choice the portal client makes
    (:func:`townrecord.records.portal.open_client`): this program talks to the
    addresses its own settings name, and not through whatever proxy the
    environment happens to carry. It also keeps a machine whose environment
    holds a proxy URL httpx cannot parse from failing to start at all.
    """
    if client is not None:
        return client
    return httpx.Client(timeout=DEFAULT_HTTP_TIMEOUT_S, trust_env=False)


def _default_probe(settings: Settings, capture_runner: CaptureRunner | None):
    """Return the known-video test of spec 8.9, run against the video the user set.

    The video comes from the settings: no URL is written here, because spec 8.9
    names no test video and a service that picked one would be fetching
    something nobody asked for. With no video set the test refuses plainly, and
    the daily check of spec 16.2 is recorded paused with the same reason.
    """
    return capture_probe(test_video_url=settings.test_video_url, runner=capture_runner)


def _interpreter_or_none(manager: RuntimeManager, *, quiet: bool = False) -> str | None:
    """Return the active runtime's interpreter, or None when none is installed.

    None is not a silent fallback: it is the documented meaning of the field
    (``capture.job`` uses this process's interpreter, and ``status`` says the
    runtime is not installed). Spec 8.9 keeps a user-site install out of the
    picture by giving the runtime its own venv; until that venv exists there is
    nothing to point at, and the service says so rather than inventing a path.
    """
    try:
        return manager.interpreter()
    except RuntimeNotInstalled as exc:
        if not quiet:
            logger.warning("No active %s runtime yet: %s", manager.tool, exc)
        return None


def _textflowkit_or_none(
    settings: Settings, runtime_settings: RuntimeSettings, *, quiet: bool = False
) -> str | None:
    """Return the TextFlowKit program of the private runtime, or None.

    None means the console script's name, found on PATH: spec 8.5 runs
    TextFlowKit as a program, and until its own runtime of spec 8.9 is
    installed the name is all there is. The transcription job records what it
    could not run, so a missing program is a reason and never a silent no-op.
    """
    try:
        manager = RuntimeManager(
            root=settings.runtime_root, tool=TEXTFLOWKIT_TOOL, settings=runtime_settings
        )
        return manager.program()
    except (RuntimeNotInstalled, ValueError, RuntimeError) as exc:
        if not quiet:
            logger.warning("No active %s runtime yet: %s", TEXTFLOWKIT_TOOL, exc)
        return None
