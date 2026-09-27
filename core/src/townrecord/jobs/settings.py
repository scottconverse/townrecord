"""Settings for the jobs system (spec 16.1).

Two lanes: `heavy` runs one job at a time and `normal` runs two. Transcription
is limited to one run at a time across both lanes. A running job whose
heartbeat is older than `heartbeat_timeout` seconds is put back in the queue
so that it resumes. Every value can be changed with a TOWNRECORD_JOBS_*
environment variable.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

#: The two lanes of spec 16.1, and the most jobs each may run at once.
DEFAULT_LANE_LIMITS: dict[str, int] = {"heavy": 1, "normal": 2}

#: Job kinds with their own limit, on top of the lane limit (spec 16.1).
DEFAULT_KIND_LIMITS: dict[str, int] = {"transcribe": 1}

#: Seconds without a heartbeat before a running job goes back to the queue.
DEFAULT_HEARTBEAT_TIMEOUT = 300.0

#: Seconds between the runner's own heartbeats for the jobs it is running.
#: Zero turns them off, which leaves heartbeats to the handler.
DEFAULT_HEARTBEAT_INTERVAL = 30.0

#: Seconds between stale checks in a lane worker.
DEFAULT_RECLAIM_INTERVAL = 5.0

#: Seconds a lane worker waits before it looks for work again.
DEFAULT_POLL_INTERVAL = 0.1

#: Lanes that exist. Anything else is refused (the table has the same check).
LANES = ("heavy", "normal")


def _number(source: Mapping[str, str], name: str, default: float) -> float:
    """Read a positive number from the environment, or keep the default."""
    text = source.get(name, "").strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class JobsSettings:
    """How many jobs may run, and how patient the runner is with them."""

    #: Lane name to the most jobs that lane may run at once.
    lane_limits: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_LANE_LIMITS))
    #: Job kind to the most jobs of that kind that may run at once.
    kind_limits: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_KIND_LIMITS))
    heartbeat_timeout: float = DEFAULT_HEARTBEAT_TIMEOUT
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL
    reclaim_interval: float = DEFAULT_RECLAIM_INTERVAL
    poll_interval: float = DEFAULT_POLL_INTERVAL

    def lanes(self) -> tuple[str, ...]:
        """Return the lanes that have at least one worker."""
        return tuple(name for name in LANES if self.lane_limit(name) > 0)

    def lane_limit(self, lane: str) -> int:
        """Return how many jobs the lane may run at once."""
        return max(0, int(self.lane_limits.get(lane, 0)))

    def kind_limit(self, kind: str) -> int | None:
        """Return the limit for a job kind, or None when the kind has none."""
        limit = self.kind_limits.get(kind)
        return None if limit is None else max(0, int(limit))

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> JobsSettings:
        """Build settings from the TOWNRECORD_JOBS_* environment variables."""
        source = os.environ if env is None else env
        lanes = dict(DEFAULT_LANE_LIMITS)
        lanes["heavy"] = int(_number(source, "TOWNRECORD_JOBS_HEAVY_LIMIT", lanes["heavy"]))
        lanes["normal"] = int(_number(source, "TOWNRECORD_JOBS_NORMAL_LIMIT", lanes["normal"]))
        kinds = dict(DEFAULT_KIND_LIMITS)
        kinds["transcribe"] = int(
            _number(source, "TOWNRECORD_JOBS_TRANSCRIBE_LIMIT", kinds["transcribe"])
        )
        return cls(
            lane_limits=lanes,
            kind_limits=kinds,
            heartbeat_timeout=_number(
                source, "TOWNRECORD_JOBS_HEARTBEAT_TIMEOUT", DEFAULT_HEARTBEAT_TIMEOUT
            ),
            heartbeat_interval=_number(
                source, "TOWNRECORD_JOBS_HEARTBEAT_INTERVAL", DEFAULT_HEARTBEAT_INTERVAL
            ),
            reclaim_interval=_number(
                source, "TOWNRECORD_JOBS_RECLAIM_INTERVAL", DEFAULT_RECLAIM_INTERVAL
            ),
            poll_interval=_number(source, "TOWNRECORD_JOBS_POLL_INTERVAL", DEFAULT_POLL_INTERVAL),
        )
