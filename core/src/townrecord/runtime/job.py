"""The `runtime_update` job (spec 8.9, 16.1, 16.2).

The daily schedule of spec 16.2 enqueues this job once a day, and the job does
the whole update in one run: ask PyPI, install a newer version if there is one,
test it against one known video, and switch only when the test passes. One job
of this kind updates one tool, which is the manager it was built with.

What the run found is left where the user can see it: a short checkpoint on the
job, and, when the update could not be applied, a pause with the plain reason
(spec 16.3: a job that cannot do its work pauses and says why, and it never
skips silently).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ..jobs import JobContext
from ..jobs.registry import Registry, default_registry
from .manager import Probe, RuntimeManager
from .settings import TOOL_NAME, RuntimeSettings

logger = logging.getLogger(__name__)

#: The job kind the schedule enqueues.
JOB_KIND = "runtime_update"

#: The lane of spec 16.1. The job waits on PyPI and on `uv`, not on a lot of
#: local computing, so it is a normal job and not a heavy one.
LANE = "normal"


@dataclass
class RuntimeUpdate:
    """The handler for the `runtime_update` job kind."""

    manager: RuntimeManager
    #: The client used for the one PyPI call. The caller builds it, so a test
    #: hands in an `httpx.MockTransport` client and no test reaches the network.
    client: httpx.Client
    #: The known-video test of spec 8.9, which gets an interpreter path.
    probe: Probe
    #: The video the probe captures, kept for the reason the user reads.
    test_video_url: str

    @property
    def tool(self) -> str:
        """The tool this job updates. One job updates one tool (spec 8.9)."""
        return self.manager.tool

    def __call__(self, ctx: JobContext) -> None:
        ctx.heartbeat()
        outcome = self.manager.check_and_update(self.test_video_url, self.probe, client=self.client)
        ctx.save_checkpoint(
            {
                "tool": self.tool,
                "checked": outcome.checked,
                "requested": outcome.requested,
                "active": outcome.active,
                "previous": outcome.previous,
                "switched": outcome.switched,
                "failed": outcome.failed,
                "reason": outcome.reason,
            }
        )
        logger.info("The %s update check finished: %s", self.manager.tool, outcome.reason)
        if outcome.failed:
            ctx.pause(outcome.reason)


@dataclass
class RuntimeUpdates:
    """The ``runtime_update`` handler when TownRecord manages more than one tool.

    The job kind is one, and the payload's ``tool`` says which tool a run
    updates (spec 8.9). A payload with no tool means yt-dlp, which is what every
    job enqueued before TextFlowKit had a runtime of its own carried. A tool
    TownRecord does not manage is a plain error rather than a silent yt-dlp run.
    """

    updates: Mapping[str, RuntimeUpdate]

    def for_payload(self, payload: Any) -> RuntimeUpdate:
        """The update a job with this payload runs."""
        tool = payload.get("tool") if isinstance(payload, Mapping) else None
        name = str(tool or TOOL_NAME).strip()
        try:
            return self.updates[name]
        except KeyError:
            managed = ", ".join(sorted(self.updates))
            raise ValueError(
                f"TownRecord does not manage a tool called {name!r}; it manages {managed}."
            ) from None

    def __call__(self, ctx: JobContext) -> None:
        self.for_payload(ctx.payload)(ctx)


def program_probe(program: str) -> Probe:
    """The test of a tool that is run as its own program, such as TextFlowKit.

    The install already asked the program for its version through the program
    itself, so a runtime that reached the test has a program that runs. The
    test checks the program is in the venv being tested, next to its python,
    which is the one thing a later step could still have lost.
    """
    from . import tools

    def probe(python: str) -> bool:
        venv = Path(python).parent.parent
        return tools.program_in(venv, program).is_file()

    return probe


def register_tools(
    *,
    root: str | Path,
    client: httpx.Client,
    probes: Mapping[str, Probe],
    test_video_urls: Mapping[str, str],
    registry: Registry | None = None,
    settings: RuntimeSettings | None = None,
    uv_path: str | None = None,
) -> RuntimeUpdates:
    """Register one ``runtime_update`` handler that updates every tool in `probes`."""
    runtime = settings or RuntimeSettings()
    handler = RuntimeUpdates(
        updates={
            tool: RuntimeUpdate(
                manager=RuntimeManager(
                    root=Path(root), settings=runtime, uv_path=uv_path, tool=tool
                ),
                client=client,
                probe=probe,
                test_video_url=test_video_urls.get(tool, ""),
            )
            for tool, probe in probes.items()
        }
    )
    (default_registry if registry is None else registry).register(JOB_KIND, handler, lane=LANE)
    return handler


def register(
    *,
    root: str | Path,
    client: httpx.Client,
    probe: Probe,
    test_video_url: str,
    registry: Registry | None = None,
    settings: RuntimeSettings | None = None,
    uv_path: str | None = None,
    tool: str = TOOL_NAME,
) -> RuntimeUpdate:
    """Register the runtime update job and return the handler.

    The app-data root is required and is never defaulted: it is the folder the
    user chose, and a runtime installed under a guess would be a runtime
    nobody asked for.

    `tool` says which tool this job updates, `yt-dlp` by default. It is passed
    straight to the manager, which is where the name lives, so the checkpoint
    and the log line cannot disagree with the folder being updated.
    """
    handler = RuntimeUpdate(
        manager=RuntimeManager(
            root=Path(root),
            settings=settings or RuntimeSettings(),
            uv_path=uv_path,
            tool=tool,
        ),
        client=client,
        probe=probe,
        test_video_url=test_video_url,
    )
    (default_registry if registry is None else registry).register(JOB_KIND, handler, lane=LANE)
    return handler
