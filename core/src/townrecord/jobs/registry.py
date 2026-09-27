"""The handler registry (spec 16.1).

A job kind is a name like `capture_video` or `transcribe`. A handler is a
function that takes one JobContext and does the work. The registry says which
handler runs a kind, and which lane that kind belongs to by default.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .settings import LANES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .queue import JobContext

#: What a handler looks like. It does the work and returns when it is done.
Handler = Callable[["JobContext"], None]


@dataclass(frozen=True)
class Registration:
    """One registered job kind."""

    kind: str
    handler: Handler
    lane: str


class Registry:
    """The job kinds a worker knows how to run."""

    def __init__(self) -> None:
        self._by_kind: dict[str, Registration] = {}

    def register(self, kind: str, handler: Handler, lane: str = "normal") -> Registration:
        """Add one job kind. Registering the same kind twice is refused."""
        if not kind or not kind.strip():
            raise ValueError("A job kind needs a name.")
        if lane not in LANES:
            raise ValueError(f"The lane must be one of {', '.join(LANES)}.")
        if not callable(handler):
            raise ValueError(f"The handler for {kind} is not callable.")
        if kind in self._by_kind:
            raise ValueError(f"A handler is already registered for {kind}.")
        entry = Registration(kind=kind, handler=handler, lane=lane)
        self._by_kind[kind] = entry
        return entry

    def handler_for(self, kind: str) -> Handler | None:
        """Return the handler for a kind, or None when nothing is registered."""
        entry = self._by_kind.get(kind)
        return None if entry is None else entry.handler

    def lane_for(self, kind: str) -> str | None:
        """Return the registered lane for a kind, or None when it has none."""
        entry = self._by_kind.get(kind)
        return None if entry is None else entry.lane

    def registration(self, kind: str) -> Registration | None:
        """Return the whole registration for a kind, or None."""
        return self._by_kind.get(kind)

    def kinds(self) -> tuple[str, ...]:
        """Return every registered kind, in order."""
        return tuple(sorted(self._by_kind))


#: The registry of the running process. Tests should build their own.
default_registry = Registry()


def register(kind: str, handler: Handler, lane: str = "normal") -> Registration:
    """Register a kind on the process-wide registry."""
    return default_registry.register(kind, handler, lane)
