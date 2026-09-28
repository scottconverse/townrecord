"""A real worker process, for the hard-stop live proof (spec 16.1, unit HB1).

`test_hard_stop_live.py` runs this file as a subprocess of its own. Nothing
imports it, and it imports no test helper: it is the real `Runner` of
`townrecord.jobs`, on a real SQLite file, in an operating system process of its
own, so that killing it really kills it. No thread is left behind to send one
more heartbeat, which is the whole point of the proof.

The mode comes from `HB1_MODE`:

``block``
    Claim the job, save a checkpoint, announce that it started, then never
    return. The runner's own heartbeat thread keeps the job alive for as long
    as this process lives, which is what a real slow handler looks like.
``resume``
    Claim the job the killed run left behind, write the checkpoint it found to
    a file, and finish the job.

The other variables are the database (`HB1_DB`), the file that says the
blocking handler is inside itself (`HB1_STARTED`), and the file the resuming
handler writes the checkpoint it read (`HB1_CHECKPOINT_SEEN`). The timings come
from the real `TOWNRECORD_JOBS_*` settings, so the proof exercises the settings
path a real run uses.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from townrecord.jobs import JobContext, JobsSettings, Registry, Runner

#: The job kind and lane this worker serves. It is not a kind the service
#: registers: the proof is about the queue's recovery, not about a handler.
KIND = "hb1_extract"
LANE = "heavy"

#: The checkpoint the blocking run saves, so the resuming run can be asked for
#: exactly it. A number no default could produce by accident.
CHECKPOINT: dict[str, int] = {"pages": 40, "of": 120}


def _marker(name: str) -> Path:
    return Path(os.environ[name])


def _block(ctx: JobContext) -> None:
    """Do a slow thing that saves where it got to, and never finish."""
    ctx.save_checkpoint(dict(CHECKPOINT))
    _marker("HB1_STARTED").write_text("started", encoding="utf-8")
    while True:  # killed from outside; a real capture or transcription sits here
        time.sleep(0.05)


def _resume(ctx: JobContext) -> None:
    """Read what the dead run left and finish the job at once."""
    found = ctx.checkpoint()
    _marker("HB1_CHECKPOINT_SEEN").write_text(json.dumps(found), encoding="utf-8")


def main() -> int:
    handler = _resume if os.environ.get("HB1_MODE") == "resume" else _block
    registry = Registry()
    registry.register(KIND, handler, lane=LANE)
    runner = Runner(
        os.environ["HB1_DB"],
        registry=registry,
        settings=JobsSettings.from_env(),
    )
    runner.start()
    print(f"worker ready pid={os.getpid()} mode={os.environ.get('HB1_MODE')}", flush=True)
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:  # pragma: no cover - a polite stop, not the proof
        runner.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
