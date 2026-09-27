"""The `runtime_update` job (spec 8.9, 16.1, 16.2).

The daily schedule of spec 16.2 enqueues this job once a day, and the job does
the whole update in one run: ask PyPI, install a newer yt-dlp if there is one,
test it against one known video, and switch only when the test passes.

What the run found is left where the user can see it: a short checkpoint on the
job, and, when the update could not be applied, a pause with the plain reason
(spec 16.3: a job that cannot do its work pauses and says why, and it never
skips silently).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..jobs import JobContext
from ..jobs.registry import Registry, default_registry
from .manager import Probe, RuntimeManager
from .settings import RuntimeSettings

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

    def __call__(self, ctx: JobContext) -> None:
        ctx.heartbeat()
        outcome = self.manager.check_and_update(self.test_video_url, self.probe, client=self.client)
        ctx.save_checkpoint(
            {
                "checked": outcome.checked,
                "requested": outcome.requested,
                "active": outcome.active,
                "previous": outcome.previous,
                "switched": outcome.switched,
                "failed": outcome.failed,
                "reason": outcome.reason,
            }
        )
        logger.info("The yt-dlp update check finished: %s", outcome.reason)
        if outcome.failed:
            ctx.pause(outcome.reason)


def register(
    *,
    root: str | Path,
    client: httpx.Client,
    probe: Probe,
    test_video_url: str,
    registry: Registry | None = None,
    settings: RuntimeSettings | None = None,
    uv_path: str | None = None,
) -> RuntimeUpdate:
    """Register the runtime update job and return the handler.

    The app-data root is required and is never defaulted: it is the folder the
    user chose, and a runtime installed under a guess would be a runtime
    nobody asked for.
    """
    handler = RuntimeUpdate(
        manager=RuntimeManager(
            root=Path(root), settings=settings or RuntimeSettings(), uv_path=uv_path
        ),
        client=client,
        probe=probe,
        test_video_url=test_video_url,
    )
    (default_registry if registry is None else registry).register(JOB_KIND, handler, lane=LANE)
    return handler
