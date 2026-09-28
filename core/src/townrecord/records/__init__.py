"""The PrimeGov records jobs: sync a portal, fetch its records, read them.

Five kinds, and the lanes spec 16.1 gives them:

* ``sync_primegov`` on the normal lane: list a window of one portal and store
  what it published (spec 7.2 step 3, 7.1 step 5);
* ``download_record`` on the heavy lane: a packet runs 60 to 170 MB, so it is
  fetched where long work belongs (spec 9.6);
* ``extract_pages`` on the heavy lane: a hundred page packet is slow to open,
  so reading one is its own job rather than part of the download (spec 9.6);
* ``align_meeting`` on the normal lane: give a meeting's agenda items their
  place in its video (spec 10.2);
* ``read_minutes`` on the normal lane: read a meeting's votes out of its
  minutes in a later session's packet, or out of its video when there are no
  minutes yet (spec 9.4, 10.4).

Importing this package registers the five kinds on the process-wide registry,
so ``enqueue`` picks the right lane and a ``Runner`` built without a registry
finds the real handlers. ``register_jobs`` is the same thing for a caller that
built its own registry, and it is safe to call twice.
"""

from __future__ import annotations

from ..jobs import Registry, default_registry
from .align import align_meeting
from .download import download_record
from .minutes import read_minutes
from .pages import extract_pages
from .portal import (
    ALIGN_MEETING,
    DOWNLOAD_RECORD,
    EXTRACT_PAGES,
    HEAVY,
    NORMAL,
    READ_MINUTES,
    SYNC_PRIMEGOV,
)
from .requests import (
    ASKED_FOR,
    MINUTES_ASKED_FOR,
    MINUTES_LOOKBACK,
    PACKET_READ,
    SYNCED_AGAIN,
    TRANSCRIPT_STORED,
    request_alignment,
    request_minutes,
    request_minutes_of_packet,
)
from .sync import sync_primegov

__all__ = [
    "ALIGN_MEETING",
    "ASKED_FOR",
    "DOWNLOAD_RECORD",
    "EXTRACT_PAGES",
    "HEAVY",
    "MINUTES_ASKED_FOR",
    "MINUTES_LOOKBACK",
    "NORMAL",
    "PACKET_READ",
    "READ_MINUTES",
    "SYNCED_AGAIN",
    "SYNC_PRIMEGOV",
    "TRANSCRIPT_STORED",
    "align_meeting",
    "download_record",
    "extract_pages",
    "jobs",
    "read_minutes",
    "register_jobs",
    "request_alignment",
    "request_minutes",
    "request_minutes_of_packet",
    "sync_primegov",
]

#: The five kinds with the lane each one runs on.
jobs: tuple[tuple[str, object, str], ...] = (
    (SYNC_PRIMEGOV, sync_primegov, NORMAL),
    (DOWNLOAD_RECORD, download_record, HEAVY),
    (EXTRACT_PAGES, extract_pages, HEAVY),
    (ALIGN_MEETING, align_meeting, NORMAL),
    (READ_MINUTES, read_minutes, NORMAL),
)


def register_jobs(registry: Registry | None = None) -> tuple[str, ...]:
    """Register these five kinds and return the kinds that were added.

    A kind that is already registered is left as it is: the caller may have
    registered its own handler for it, and a second registration is refused by
    the registry.
    """
    target = default_registry if registry is None else registry
    added = []
    for kind, handler, lane in jobs:
        if target.registration(kind) is not None:
            continue
        target.register(kind, handler, lane)  # type: ignore[arg-type]
        added.append(kind)
    return tuple(added)


register_jobs()
