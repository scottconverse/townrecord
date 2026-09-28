"""The daily schedule (spec 16.2, 8.9).

The service runs a schedule of its own: one ``sync_primegov`` per portal source
the user accepted, once per local day, at a local time the user chose in the
area's time zone, plus the daily ``runtime_update`` check of spec 8.9. This
module is that schedule and nothing else. It does not run a job and it does not
fetch anything: it enqueues, and the jobs system of spec 16.1 does the work.

Three rules are what the code is built around.

**Once per local day.** The day a run belongs to is the local date in the
configured zone, never the UTC date, and it is written in ``schedule_runs``
under a UNIQUE key of task, subject and local date (migration 0012). A service
that restarts ten times in one morning still syncs the morning once, and a run
at 22:00 in Denver belongs to that Denver day rather than to tomorrow in UTC.

**A run that cannot happen pauses, and says why.** An accepted portal source
with no origin and a yt-dlp check with no test video are each recorded as a
paused run carrying a plain sentence (spec 16.2, 16.3). A paused run holds the
day: it is that day's record, so the same refusal is not written again on every
tick, and the next local day tries again. A user who fixes the cause runs the
sync by hand, which is what a job's origin is for.

What a day is owed is one sync per portal source the user accepted (spec 16.2
says "per body"; the job is per source, so the source is the subject). A
suggested source has not been accepted yet and a rejected one was turned down,
and neither is a run that cannot happen: no run is owed for them, so none is
recorded and none is skipped silently either -- ``townrecord status`` lists
every source with its status, which is where the user reads that they are not
being synced. A source that answers with failures is ``broken`` and **is**
still a run: the sync is how a broken source recovers (spec 7.3).

**Time is injected.** The clock is a field of this object, so a test moves it
across a daylight saving change and watches one run per local day come out.

A service with no time zone configured has no local time to place a run in.
Rather than pick a zone for the user (rule D), the schedule records each of the
day's runs as paused with a sentence naming the setting, and ``townrecord
status`` shows it.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import DEFAULT_DAILY_TIME
from .db import connect as default_connect
from .jobs import ORIGIN_SCHEDULED, Clock, Registry, enqueue, utcnow
from .records.portal import (
    MEETING_PORTAL,
    SYNC_PRIMEGOV,
    SYNCABLE_STATUSES,
    SyncRefused,
    syncable_source,
)
from .repo import all_sources, record_enqueued_run, record_paused_run, run_for
from .repo.rows import Source
from .runtime.job import JOB_KIND as RUNTIME_UPDATE_KIND
from .runtime.settings import TOOL_NAME

logger = logging.getLogger(__name__)

#: The tasks the schedule knows. They are the job kinds it enqueues.
TASK_SYNC = SYNC_PRIMEGOV
TASK_RUNTIME_UPDATE = RUNTIME_UPDATE_KIND

#: The zone the schedule counts days in when the user has not named one. It is
#: not a guess about the area: it is the one zone every clock has, and the
#: reason recorded with the run says no zone was configured.
UNCONFIGURED_ZONE = "UTC"

#: The sentence a run carries when the service has no time zone to place it in.
NO_ZONE_REASON = (
    "No time zone is set, so the daily schedule has no local time to run at. "
    "Set TOWNRECORD_TIME_ZONE to the area's zone, for example America/Denver."
)

#: The sentence the yt-dlp check carries when there is no test video for a new
#: version to be tested against (spec 8.9).
NO_TEST_VIDEO_REASON = (
    "No test video is set, so a new yt-dlp cannot be tested before it is used. "
    "Set TOWNRECORD_TEST_VIDEO to the one known video the update is tested against."
)

#: The sentence a meeting portal source with no origin carries. A sync reads the
#: portal at the source's own origin, and there is nowhere else one could come
#: from (rule D).
NO_ORIGIN_REASON = (
    "Source {source_id} is a meeting portal with no origin, so a daily sync has no portal to read."
)

#: How often the schedule looks at the clock. The interval decides how late a
#: run may be, never whether it happens.
DEFAULT_INTERVAL_S = 30.0

#: How long `stop` waits for the schedule thread.
DEFAULT_STOP_TIMEOUT = 5.0

#: A function that opens the database, so a test can hand in its own.
Connect = Callable[[str | Path], sqlite3.Connection]

#: One run the day is owed: the job kind, the subject it is recorded under, why
#: it cannot happen (or None), and the payload the job is enqueued with.
Planned = tuple[str, str, str | None, dict[str, Any]]


@dataclass(frozen=True)
class DueDay:
    """The local day the runs are owed for, and the zone it was read in."""

    local_date: str
    time_zone: str


@dataclass
class Scheduler:
    """The daily runs of one service instance (spec 16.2).

    ``tick`` is the whole schedule: it decides which runs the current local day
    is owed and enqueues them. ``start`` does the same thing on a thread for as
    long as the service runs.
    """

    db_path: str | Path
    registry: Registry | None = None
    #: The IANA name of the area's time zone (spec 16.2). Empty is a state the
    #: schedule reports rather than a problem it solves by guessing.
    time_zone: str = ""
    #: The local time the day's runs are owed at, in `time_zone`.
    daily_time: time = DEFAULT_DAILY_TIME
    #: The one known video the yt-dlp check is tested against (spec 8.9).
    test_video_url: str = ""
    clock: Clock = utcnow
    connect: Connect = default_connect
    interval_s: float = DEFAULT_INTERVAL_S
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    # -- the clock and the zone --------------------------------------------

    def zone(self) -> ZoneInfo | None:
        """Return the configured zone, or None when there is not a usable one."""
        name = self.time_zone.strip()
        if not name:
            return None
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            return None

    def due_day(self, moment: datetime) -> DueDay | None:
        """Return the local day the runs are owed for now, or None.

        The day is owed at any moment at or after ``daily_time`` on it, and not
        before. Comparing the wall clock of the moment with the chosen time is
        what makes a daylight saving change a non-event: the local date turns at
        local midnight and the time of day is read as the area writes it, so the
        runs happen once on the day the clocks move either way.

        A service with no usable zone is owed the day as soon as its UTC day is
        one nothing has been recorded for, because there is no local time to
        place it in. The runs are then recorded paused rather than skipped.
        """
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo(UNCONFIGURED_ZONE))
        zone = self.zone()
        if zone is None:
            return DueDay(
                local_date=moment.astimezone(ZoneInfo(UNCONFIGURED_ZONE)).date().isoformat(),
                time_zone=UNCONFIGURED_ZONE,
            )
        local = moment.astimezone(zone)
        if local.time() < self.daily_time:
            return None
        return DueDay(local_date=local.date().isoformat(), time_zone=zone.key)

    # -- the runs ----------------------------------------------------------

    def tick(self) -> tuple[int, ...]:
        """Enqueue the runs the current local day is owed. Return their job ids.

        The whole tick is one transaction, so a job and the row that records the
        run are written together: a service that dies midway leaves either both
        or neither, and the next tick cannot enqueue the same day twice.
        """
        day = self.due_day(self.clock())
        if day is None:
            return ()
        conn = self.connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                job_ids = list(self._run_day(conn, day))
                conn.execute("COMMIT")
            except BaseException:
                _rollback_quietly(conn)
                raise
        finally:
            conn.close()
        for job_id in job_ids:
            logger.info("The daily schedule enqueued job %s for %s.", job_id, day.local_date)
        return tuple(job_ids)

    def run_once(self) -> tuple[int, ...]:
        """Run one tick, logging a database that is not ready yet.

        The service migrates the database before it starts the schedule, so a
        failure here is a database another process holds or one written by an
        older version. Either way the schedule tries again rather than ending.
        """
        try:
            return self.tick()
        except sqlite3.Error as exc:
            logger.warning("The daily schedule could not read the database: %s", exc)
            return ()

    def _run_day(self, conn: sqlite3.Connection, day: DueDay) -> Iterator[int]:
        """Enqueue or refuse each of the day's runs, once each."""
        unplaced = self.zone() is None
        for task, subject, refusal, payload in self._plan(conn):
            if run_for(conn, task, subject, day.local_date) is not None:
                continue
            if refusal is None and unplaced:
                refusal = NO_ZONE_REASON
            if refusal is not None:
                record_paused_run(
                    conn,
                    task=task,
                    subject=subject,
                    local_date=day.local_date,
                    time_zone=day.time_zone,
                    reason=refusal,
                )
                logger.warning("The daily %s of %s paused: %s", task, subject, refusal)
                continue
            job_id = enqueue(
                conn,
                task,
                payload,
                origin=ORIGIN_SCHEDULED,
                registry=self.registry,
                clock=self.clock,
            )
            record_enqueued_run(
                conn,
                task=task,
                subject=subject,
                local_date=day.local_date,
                time_zone=day.time_zone,
                job_id=job_id,
            )
            yield job_id

    def _plan(self, conn: sqlite3.Connection) -> Iterator[Planned]:
        """Yield every run a day is owed, with why it cannot happen when it cannot.

        One sync per meeting portal source that may be synced, then the yt-dlp
        update check. The subjects are what "once per local day" is counted by.

        A source the user has not accepted, or has rejected, is not yielded at
        all: no run is owed for it, so there is nothing to pause and nothing to
        skip. Its status is what ``townrecord status`` shows.
        """
        for source in all_sources(conn):
            if source.type != MEETING_PORTAL or source.status not in SYNCABLE_STATUSES:
                continue
            yield (
                TASK_SYNC,
                source_subject(source.id),
                self._sync_refusal(conn, source),
                {"source_id": source.id},
            )
        yield (
            TASK_RUNTIME_UPDATE,
            tool_subject(TOOL_NAME),
            None if self.test_video_url.strip() else NO_TEST_VIDEO_REASON,
            {"tool": TOOL_NAME},
        )

    def _sync_refusal(self, conn: sqlite3.Connection, source: Source) -> str | None:
        """Why one source cannot be synced today, or None when it can.

        The sentences are the ones the sync job itself would refuse with
        (:func:`townrecord.records.portal.syncable_source`), so the reason the
        user reads is the same one the job would have given.
        """
        if not source.origin.strip():
            return NO_ORIGIN_REASON.format(source_id=source.id)
        try:
            syncable_source(conn, source.id)
        except SyncRefused as exc:
            return str(exc)
        return None

    # -- the thread --------------------------------------------------------

    def start(self) -> None:
        """Run the schedule on a thread until `stop` is called."""
        if self._thread is not None:
            raise RuntimeError("This schedule is already started.")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="townrecord-schedule", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = DEFAULT_STOP_TIMEOUT) -> bool:
        """Ask the schedule to stop and wait for it. True when it stopped."""
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is None:
            return True
        thread.join(timeout)
        if thread.is_alive():
            logger.warning("The daily schedule did not stop within the timeout.")
            return False
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.interval_s)


def local_today(moment: datetime, zone_name: str) -> date:
    """Return the local date of one moment in one zone, configured or not."""
    zone = ZoneInfo(zone_name.strip() or UNCONFIGURED_ZONE)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo(UNCONFIGURED_ZONE))
    return moment.astimezone(zone).date()


def day_of(conn: sqlite3.Connection, task: str, subject: str, local_date: str):
    """Read one day's record of one run, or None. A thin re-export of the repo."""
    return run_for(conn, task, subject, local_date)


def source_subject(source_id: int) -> str:
    """Return the subject one portal source's daily sync is recorded under."""
    return f"source:{source_id}"


def tool_subject(tool: str) -> str:
    """Return the subject one tool's daily check is recorded under."""
    return f"tool:{tool}"


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    """Roll back a transaction that has nothing left to roll back."""
    with contextlib.suppress(sqlite3.Error):
        conn.execute("ROLLBACK")


__all__ = [
    "DEFAULT_INTERVAL_S",
    "DEFAULT_STOP_TIMEOUT",
    "NO_ORIGIN_REASON",
    "NO_TEST_VIDEO_REASON",
    "NO_ZONE_REASON",
    "TASK_RUNTIME_UPDATE",
    "TASK_SYNC",
    "UNCONFIGURED_ZONE",
    "Connect",
    "DueDay",
    "Scheduler",
    "day_of",
    "local_today",
    "source_subject",
    "tool_subject",
]
