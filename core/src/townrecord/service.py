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
when none is -- and with an ``httpx`` client for the one PyPI call. Nothing
here reaches the network: a client is built, and no request is made until a
job runs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from . import records
from .capture import JOB_KIND as CAPTURE_JOB_KIND
from .capture import TRANSCRIBE_JOB_KIND, register_transcribe
from .capture import register as register_capture
from .capture.command import Runner as CaptureRunner
from .capture.probe import capture_probe
from .config import Settings
from .db import connect as default_connect
from .jobs import Clock, JobsSettings, Registry, Runner, utcnow
from .records import ALIGN_MEETING, DOWNLOAD_RECORD, EXTRACT_PAGES, SYNC_PRIMEGOV
from .runtime import JOB_KIND as RUNTIME_UPDATE_KIND
from .runtime import RuntimeManager, RuntimeNotInstalled, RuntimeSettings
from .runtime import register as register_runtime_update
from .runtime.settings import TEXTFLOWKIT_TOOL, TOOL_NAME
from .schedule import Scheduler

logger = logging.getLogger(__name__)

#: Every job kind that exists, with the module that registers it (spec 16.1).
#: The order is the order they are wired in. This is the list the service
#: registers and the list ``townrecord status`` counts against.
KIND_MODULES: tuple[tuple[str, str], ...] = (
    (SYNC_PRIMEGOV, "townrecord.records"),
    (DOWNLOAD_RECORD, "townrecord.records"),
    (EXTRACT_PAGES, "townrecord.records"),
    (ALIGN_MEETING, "townrecord.records"),
    (CAPTURE_JOB_KIND, "townrecord.capture"),
    (TRANSCRIBE_JOB_KIND, "townrecord.capture"),
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
    interpreter = _interpreter_or_none(manager)
    real_client = _client_or_new(client)
    handlers = _register_handlers(
        settings,
        registry,
        client=real_client,
        probe=probe if probe is not None else _default_probe(settings, capture_runner),
        capture_runner=capture_runner,
        interpreter=interpreter,
        runtime_settings=runtime,
        textflowkit=_textflowkit_or_none(settings, runtime),
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
    return Service(
        settings=settings,
        registry=registry,
        runner=runner,
        scheduler=scheduler,
        handlers=handlers,
        client=real_client,
    )


def _register_handlers(
    settings: Settings,
    registry: Registry,
    *,
    client: httpx.Client,
    probe: Callable[[str], bool],
    capture_runner: CaptureRunner | None,
    interpreter: str | None,
    runtime_settings: RuntimeSettings,
    textflowkit: str | None,
) -> dict[str, object]:
    """Wire each handler with the paths and programs it runs."""
    handlers: dict[str, object] = {
        CAPTURE_JOB_KIND: register_capture(
            storage_root=settings.storage_root,
            registry=registry,
            runner=capture_runner,
            interpreter=interpreter,
        ),
        TRANSCRIBE_JOB_KIND: register_transcribe(
            storage_root=settings.storage_root,
            registry=registry,
            runner=capture_runner,
            interpreter=interpreter,
            textflowkit_program=textflowkit,
        ),
        RUNTIME_UPDATE_KIND: register_runtime_update(
            root=settings.runtime_root,
            registry=registry,
            client=client,
            probe=probe,
            test_video_url=settings.test_video_url,
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


def _interpreter_or_none(manager: RuntimeManager) -> str | None:
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
        logger.warning("No active %s runtime yet: %s", manager.tool, exc)
        return None


def _textflowkit_or_none(settings: Settings, runtime_settings: RuntimeSettings) -> str | None:
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
        logger.warning("No active %s runtime yet: %s", TEXTFLOWKIT_TOOL, exc)
        return None
