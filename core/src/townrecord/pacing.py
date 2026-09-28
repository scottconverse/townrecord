"""One pace between the YouTube requests this process makes (spec 8.10).

Spec 8.10 says "Pace every request". Until this module the only pacing was
inside one yt-dlp command: the ``--sleep-subtitles 2`` and ``--sleep-requests
1`` of spec 8.3 space the requests *that command* makes, and say nothing about
the command before it or the one after it.

That was measured on 2026-09-27 from one home connection: one live run made 7
captures and 7 status asks in 80 seconds, and YouTube answered HTTP 429 to 6 of
the 7 captures. Every one of those 14 requests was a separate short-lived
command, each of which paced only itself. The deferral worked and the run did
not lose work, but the address was asking far faster than YouTube serves.

So this module holds one pace for the whole process, and every part of
TownRecord that asks YouTube something waits on it first:

* a caption capture and an audio download, which both run through
  :func:`townrecord.capture.command.run_capture`;
* a status ask, which is spec 8.2's third step;
* a channel listing, which is the feed reader and the yt-dlp reader of spec 8.1.

Two numbers, both settings:

``minimum_gap_s`` (``TOWNRECORD_PACE_MINIMUM_GAP``, default 10 seconds)
    The shortest time between the starts of two YouTube requests, whatever
    made them. Ten seconds is at most six requests a minute from one address,
    which is slower than the rate the measured run was refused at, and it
    costs a capture nothing: a capture already takes seconds to minutes. It is
    deliberately longer than yt-dlp's own ``--sleep-requests 1``, because that
    one only spaces the requests inside a single command.

``rate_limit_backoff_s`` (``TOWNRECORD_PACE_RATE_LIMIT_BACKOFF``, default 900
seconds)
    How long every YouTube request waits after one of them was answered with
    HTTP 429. Fifteen minutes is the number
    :data:`townrecord.capture.transcribe.DEFAULT_RATE_LIMIT_RETRY_S` already
    defers a rate-limited job by, so the job's own retry and the process-wide
    hold lift together instead of one waiting on the other.

The hold is on the process, not on the job that was refused: a 429 is YouTube
answering for the address, so every other YouTube job waits with the one that
got it. That is the whole point of "pace every request".

The next allowed time is written to a small JSON file under the app-data root
(``runtime_root``, spec 8.6) so a restart keeps the hold instead of walking
straight back into the same 429. The write is the atomic one the runtime
pointer uses (spec 8.6): a temporary file beside it, flushed and ``fsync``ed,
then ``os.replace``d over the old one. A pacer with no state path keeps the
time in memory only, which is what a test and a handler that no service wired
get.

Nothing here sleeps in a test: the clock and the sleeper are injected, and a
test moves its own clock forward. :func:`default_sleep` is ``time.sleep`` and
is the only thing in the module that waits on a real wall clock.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .jobs.queue import utcnow

logger = logging.getLogger(__name__)

#: A function that returns the current time. Tests pass a clock they control.
Clock = Callable[[], datetime]

#: A function that waits. Tests pass one that records the delay instead.
Sleeper = Callable[[float], None]

#: The version of the state file, so a later change to it is a visible one.
PACE_VERSION = 1

#: The state file under the app-data root. The name says what is paced.
PACE_FILE_NAME = "youtube-pace.json"

#: The format of a timestamp in the state file, which is the queue's own
#: (:func:`townrecord.jobs.queue.stamp`), so one reader knows both files.
_STAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

#: Seconds between the starts of two YouTube requests. See the module docstring
#: for why ten.
DEFAULT_MINIMUM_GAP_S = 10.0

#: Seconds every YouTube request waits after one of them was answered 429.
DEFAULT_RATE_LIMIT_BACKOFF_S = 900.0

#: What yt-dlp says when YouTube rate limits it. The wording is not fixed by
#: anything, so the check is a case-insensitive search for any of these and the
#: one that matched is kept for the user. The module lives here rather than in
#: ``capture.command`` because the status ask and the listing of ``video``
#: detect a 429 too, and neither of them should have to import the capture
#: layer to do it. ``capture.command`` re-exports both names.
RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "http error 429",
    "http 429",
    "429 too many requests",
    "too many requests",
)

#: The HTTP status that means the same thing over the portal-shaped client of
#: the feed reader, which reads a status code rather than a sentence.
RATE_LIMIT_STATUS = 429


def rate_limit_marker(stderr: str | None) -> str | None:
    """Return the text that says YouTube rate limited us, or None.

    Matching is case-insensitive, and the matched needle is returned so the
    reason the user reads quotes what yt-dlp actually printed.
    """
    text = (stderr or "").lower()
    for marker in RATE_LIMIT_MARKERS:
        if marker in text:
            return marker
    return None


def default_sleep(seconds: float) -> None:
    """Wait on the real clock. The only thing in this module that sleeps."""
    time.sleep(seconds)


def _number(source: Mapping[str, str], name: str, default: float) -> float:
    """Read a number from the environment, or keep the default.

    Blank, unreadable and negative all keep the default. Zero is allowed and
    means what it says: a caller that sets the gap to zero has turned the pace
    off, which is what a test and an operator debugging one request want.
    """
    text = source.get(name, "").strip()
    if not text:
        return default
    try:
        value = float(text)
    except ValueError:
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class PaceSettings:
    """How far apart two YouTube requests are, and how long a 429 holds them."""

    minimum_gap_s: float = DEFAULT_MINIMUM_GAP_S
    rate_limit_backoff_s: float = DEFAULT_RATE_LIMIT_BACKOFF_S

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> PaceSettings:
        """Build the settings from the TOWNRECORD_PACE_* environment variables."""
        source = os.environ if env is None else env
        return cls(
            minimum_gap_s=_number(source, "TOWNRECORD_PACE_MINIMUM_GAP", DEFAULT_MINIMUM_GAP_S),
            rate_limit_backoff_s=_number(
                source, "TOWNRECORD_PACE_RATE_LIMIT_BACKOFF", DEFAULT_RATE_LIMIT_BACKOFF_S
            ),
        )


def _parse_stamp(text: Any) -> datetime | None:
    """Read a timestamp this module wrote, or None when it is not one."""
    if not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text.strip(), _STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def _write_json(path: Path, value: Any) -> None:
    """Write JSON where a reader sees the whole file or none of it (spec 8.6).

    A temporary file in the same folder, flushed and ``fsync``ed, then moved
    over the target. A reader of the target therefore never sees half a file,
    and a process that dies mid-write leaves the old one in place.
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


class Pacer:
    """One pace for every YouTube request the process makes (spec 8.10).

    ``wait`` is what a caller does before it asks YouTube something, and
    ``rate_limited`` is what it does when the answer was HTTP 429. Both take a
    short ``what`` that names the request in the log line, so a run that is
    waiting says what it is waiting to do.
    """

    def __init__(
        self,
        *,
        settings: PaceSettings | None = None,
        state_path: str | Path | None = None,
        clock: Clock = utcnow,
        sleep: Sleeper = default_sleep,
    ) -> None:
        self.settings = settings or PaceSettings()
        #: Where the next allowed time is written, or None to keep it in memory.
        self.state_path = None if state_path is None else Path(state_path)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed, self._reason = self._read_state()

    # -- the two things a caller does ---------------------------------------

    def wait(self, *, what: str) -> float:
        """Wait until this process may ask YouTube again. Return the seconds waited.

        The slot is taken before the wait, so two threads that reach this at
        the same moment are a gap apart rather than side by side. The sleep
        happens outside the lock, and the time is read again when it is over:
        a 429 that another job hit while this one was waiting pushes the wait
        further out instead of being missed.
        """
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                if self._next_allowed is None or now >= self._next_allowed:
                    self._reserve(now, what)
                    return waited
                delay = (self._next_allowed - now).total_seconds()
                reason = self._reason
            logger.info(
                "Pacing YouTube: %.1f seconds before %s (the pace is held by %s).",
                delay,
                what,
                reason or "the previous request",
            )
            self._sleep(delay)
            waited += delay

    def rate_limited(
        self, *, what: str, marker: str = "", delay_s: float | None = None
    ) -> datetime:
        """Hold every YouTube request after one was rate limited. Return the moment.

        The hold is the process's, not the job's: every other YouTube job waits
        with the one that was refused, because a 429 is YouTube answering for
        the address. An already-longer hold is kept rather than shortened.
        """
        seconds = self.settings.rate_limit_backoff_s if delay_s is None else max(0.0, delay_s)
        with self._lock:
            now = self._clock()
            until = now + timedelta(seconds=seconds)
            if self._next_allowed is None or until > self._next_allowed:
                self._next_allowed = until
            self._reason = f"{what} was rate limited" + (f" ({marker})" if marker else "")
            self._write_state()
            return until

    # -- what the pacer is holding, for a person and for a test --------------

    def next_allowed_at(self) -> datetime | None:
        """Return the moment the next YouTube request may start, or None."""
        with self._lock:
            return self._next_allowed

    def held_for(self) -> float:
        """Return the seconds until the next request may start, 0.0 when none is held."""
        with self._lock:
            if self._next_allowed is None:
                return 0.0
            return max(0.0, (self._next_allowed - self._clock()).total_seconds())

    def held_by(self) -> str:
        """Return a plain phrase saying what is holding the pace, or an empty string."""
        with self._lock:
            return self._reason

    # -- the state file ------------------------------------------------------

    def _reserve(self, now: datetime, what: str) -> None:
        """Take the next slot. Called with the lock held."""
        self._next_allowed = now + timedelta(seconds=self.settings.minimum_gap_s)
        self._reason = f"{what} was the last request"
        self._write_state()

    def _read_state(self) -> tuple[datetime | None, str]:
        """Read the hold a previous run left behind, or nothing."""
        if self.state_path is None or not self.state_path.is_file():
            return None, ""
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning(
                "The YouTube pace file %s could not be read (%s), so it is ignored.",
                self.state_path,
                exc,
            )
            return None, ""
        if not isinstance(data, Mapping):
            return None, ""
        return _parse_stamp(data.get("next_allowed_at")), str(data.get("reason") or "")

    def _write_state(self) -> None:
        """Write the hold where a restart reads it. Called with the lock held.

        A pace that cannot be written is still a pace: the hold in memory is
        the one this process uses, and the file is only what a restart reads.
        So a failure here is a warning and never a failed job.
        """
        if self.state_path is None:
            return
        moment = self._next_allowed
        try:
            _write_json(
                self.state_path,
                {
                    "version": PACE_VERSION,
                    "next_allowed_at": None if moment is None else stamp_text(moment),
                    "reason": self._reason,
                },
            )
        except OSError as exc:
            logger.warning("The YouTube pace could not be written to %s: %s", self.state_path, exc)


def stamp_text(moment: datetime) -> str:
    """Return the timestamp text this module writes and reads.

    It is the queue's own format (``townrecord.jobs.queue.stamp``) spelled
    here so the two files are read by one rule, and a naive datetime is read as
    UTC the same way.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime(_STAMP_FORMAT)


#: The pace this process uses unless a service replaced it. Built on first
#: use, so importing this module opens no file.
_default: Pacer | None = None
_default_lock = threading.Lock()


def use_pacer(value: Pacer | None) -> None:
    """Make ``value`` the pace this process uses. None builds a fresh one later."""
    global _default
    with _default_lock:
        _default = value


def forget_pacer(value: Pacer | None) -> bool:
    """Give up the process pace, if ``value`` is the one that is installed.

    True when it was. A service installs the pace it built and takes it away
    again when it closes, and it takes away only its own: a caller that
    replaced the pace (a test, a later unit that wants a different state file)
    keeps the one it installed.
    """
    global _default
    with _default_lock:
        if _default is value:
            _default = None
            return True
        return False


def pacer() -> Pacer:
    """Return the pace this process makes its YouTube requests with.

    A service builds one pacer and hands it to every handler, so the whole
    process shares one pace and one state file. A handler built outside a
    service (a test, a script) gets this one instead of a second pace, so
    nothing about pacing depends on who wired the handler.
    """
    global _default
    with _default_lock:
        if _default is None:
            _default = Pacer()
        return _default


__all__ = [
    "DEFAULT_MINIMUM_GAP_S",
    "DEFAULT_RATE_LIMIT_BACKOFF_S",
    "PACE_FILE_NAME",
    "PACE_VERSION",
    "RATE_LIMIT_MARKERS",
    "RATE_LIMIT_STATUS",
    "Clock",
    "PaceSettings",
    "Pacer",
    "Sleeper",
    "default_sleep",
    "forget_pacer",
    "pacer",
    "rate_limit_marker",
    "stamp_text",
    "use_pacer",
]
