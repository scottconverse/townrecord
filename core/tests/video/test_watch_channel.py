"""The watch job: one channel source becomes rows and capture jobs (spec 8.1,
8.2, 7.2 step 7, 16.2, 16.3).

A watch reads the channel the source's own origin names (rule D), classifies
every listed video with that source's own settings, stores one row per video,
links it to a meeting of the body its title names, and queues one
``capture_captions`` for each meeting video that is neither captured nor
already queued. An upcoming or live video is counted and left for a later watch
(spec 8.2). A listing that finds nothing records which methods were tried and
what each answered, because a zero from a reader that was never tried is not a
fact about the channel (spec 16.3).

No test reaches the network. Each listing step is a stub fed to a real ladder,
and the City of Longmont feed is the recorded fixture of the oversight
repository, read in place and skipped when it is not on this machine. Nothing
here downloads a video: the capture handler is a stand-in that records that its
job ran (rule 7).
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from datetime import time as clock_time
from pathlib import Path
from typing import Any

import httpx
import pytest

from townrecord.capture import LANE as CAPTURE_LANE
from townrecord.capture.command import JOB_KIND as CAPTURE_JOB_KIND
from townrecord.jobs import (
    ORIGIN_MANUAL,
    ORIGIN_SCHEDULED,
    QUEUED,
    RUNNING,
    JobContext,
    Registry,
    Runner,
    enqueue,
    read_checkpoint,
)
from townrecord.records import portal
from townrecord.repo import (
    get_video,
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_source,
    insert_video,
    set_capture_state,
    videos_of_platform,
)
from townrecord.schedule import TASK_WATCH, Scheduler
from townrecord.video.watch import JOB_KIND, LANE, ZERO_VIDEOS_NOTE, register
from townrecord.video.youtube.classify import MeetingClassifier
from townrecord.video.youtube.ladder import ListingLadder
from townrecord.video.youtube.listing import (
    SOURCE_DATA_API,
    SOURCE_RSS,
    SOURCE_YTDLP,
    VideoListing,
)
from townrecord.video.youtube.readiness import READINESS_WAITING_REASON
from townrecord.video.youtube.rss import RssLister

from .conftest import CITY_CHANNEL_ID, offline_client

#: The recorded fixtures of the oversight repository. Override the folder with
#: ``TOWNRECORD_EVIDENCE`` when the recordings live somewhere else, and every
#: test that reads one skips when it is not there.
EVIDENCE_FOLDER = Path(
    os.environ.get(
        "TOWNRECORD_EVIDENCE",
        Path(__file__).resolve().parents[4] / "townrecord-oversight" / "evidence" / "fixtures",
    )
)

#: The City of Longmont channel's public feed, recorded from the live channel:
#: fifteen entries, none of them carrying a status.
CITY_RSS = EVIDENCE_FOLDER / "youtube" / f"rss-{CITY_CHANNEL_ID}.xml"

#: The channel URL the tests' source is configured with (spec 7.2 step 3).
CHANNEL_URL = f"https://www.youtube.com/channel/{CITY_CHANNEL_ID}"

#: A different channel, for the check that one source's settings are its own.
OTHER_CHANNEL_URL = "https://www.youtube.com/channel/UCXFW3IRzfCc6q_XM-uutAmw"

#: The capture stand-in's two behaviours. ``CAPTURED`` leaves the row the real
#: job leaves behind; ``WAITING`` puts the job back in the queue with a delay,
#: which is what the real job does for a video whose status metadata has not
#: arrived (spec 8.2).
CAPTURED = "captions"
WAITING = "waiting"

#: A moment after the day's runs are owed in Denver: 08:00 MDT on 27 September.
A_MORNING = datetime(2026, 9, 27, 14, 0, tzinfo=UTC)

#: The local time the day's runs are owed at, in the zone below.
DAILY_TIME = clock_time(6, 0)

DENVER = "America/Denver"

#: The published value the recorded City feed carries for its 22 September
#: session, used as the default so a stub listing is dated like the real one.
A_PUBLISHED = "2026-09-23T18:53:52+00:00"


def needs(*paths: Path) -> pytest.MarkDecorator:
    """Skip a test when a recorded fixture is not on this machine."""
    missing = ", ".join(str(path) for path in paths if not path.exists())
    return pytest.mark.skipif(bool(missing), reason=f"recorded fixture not present: {missing}")


def listed(
    video_id: str,
    title: str,
    published: str | None = A_PUBLISHED,
    *,
    method: str = SOURCE_RSS,
    **extra: Any,
) -> VideoListing:
    """One video, as a listing step saw it."""
    return VideoListing(
        video_id=video_id,
        title=title,
        published=published,
        channel_id=CITY_CHANNEL_ID,
        source_method=method,
        **extra,
    )


class StubLister:
    """A listing step that answers with what a test hands it.

    It is fed to a real :class:`ListingLadder` as one of its steps, so the
    ladder still makes its own decisions: an empty answer is a refusal in the
    method's own words and the next step is tried, and every step that refused
    is named in the failure (spec 8.1).
    """

    def __init__(self, videos: Sequence[VideoListing]) -> None:
        self.videos = list(videos)

    def list_videos(self, *_: Any) -> list[VideoListing]:
        return list(self.videos)


class World:
    """One city, its bodies, its meetings and the sources a test watches."""

    def __init__(self, conn: sqlite3.Connection, *, name: str = "Test City") -> None:
        self.conn = conn
        self.jurisdiction_id = insert_jurisdiction(conn, type="city", name=name)
        self.body_id = insert_body(conn, jurisdiction_id=self.jurisdiction_id, name="City Council")

    def body(self, name: str) -> int:
        """Store one more body of this city and return its id."""
        return insert_body(self.conn, jurisdiction_id=self.jurisdiction_id, name=name)

    def source(
        self,
        *,
        type: str = portal.VIDEO_CHANNEL,
        origin: str = CHANNEL_URL,
        status: str = "accepted",
        body_id: int | None = None,
        settings: str = "{}",
    ) -> int:
        """Store one source of this city and return its id."""
        return insert_source(
            self.conn,
            jurisdiction_id=self.jurisdiction_id,
            type=type,
            origin=origin,
            suggested_by="a person",
            reason="the tests need one",
            status=status,
            body_id=body_id,
            settings=settings,
        )

    def channel(self, **kwargs: Any) -> int:
        """Store one accepted video channel source of this city."""
        return self.source(**kwargs)

    def meeting(self, body_id: int, starts_at: str, title: str, *, type: str = "regular") -> int:
        """Store one meeting of a body of this city."""
        return insert_meeting(
            self.conn, body_id=body_id, starts_at=starts_at, title=title, type=type
        )


@pytest.fixture
def world(conn: sqlite3.Connection) -> World:
    """A city with a body, ready for a channel source."""
    return World(conn)


class Watch:
    """A watch job over one database, with a stand-in for its capture child."""

    def __init__(
        self,
        db_path: Path,
        conn: sqlite3.Connection,
        *,
        ladder: ListingLadder,
        capture: str = CAPTURED,
    ) -> None:
        self.db_path = db_path
        self.conn = conn
        self.ladder = ladder
        self.capture = capture
        #: The video ids the capture stand-in ran for, in order.
        self.capture_runs: list[int] = []
        self.registry = Registry()
        register(registry=self.registry, ladder=ladder)
        self.registry.register(CAPTURE_JOB_KIND, self._capture_job, lane=CAPTURE_LANE)

    def _capture_job(self, ctx: JobContext) -> None:
        """Stand in for the real ``capture_captions`` job.

        The real job fetches the captions and leaves the video row captured.
        Two things a watch can see are what this does instead: it leaves the row
        in the state the test asked for, or it defers and stays in the queue,
        which is how a video whose status metadata has not arrived behaves
        (spec 8.2). Nothing here downloads anything (rule 7).
        """
        video_id = int(ctx.payload["video_id"])
        self.capture_runs.append(video_id)
        if self.capture == WAITING:
            ctx.defer(READINESS_WAITING_REASON, delay_s=3600.0)
            return
        set_capture_state(ctx.conn, video_id, self.capture)

    def queue(self, source_id: int, *, origin: str = ORIGIN_MANUAL) -> int:
        """Queue one watch of one source, on the lane its kind belongs to."""
        return enqueue(
            self.conn, JOB_KIND, {"source_id": source_id}, origin=origin, registry=self.registry
        )

    def run_one(self) -> int | None:
        """Run the next claimable job of the watch's lane, if there is one."""
        return Runner(self.db_path, registry=self.registry).run_once(LANE)

    def run_until(self, job_id: int) -> None:
        """Run the lane until the named job has settled.

        A watch never defers and never pauses, so its job ends done or failed,
        and both are an end. A capture job the watch queued is left in the
        queue: a test that wants it run asks for that itself.
        """
        while self.job(job_id)["state"] in (QUEUED, RUNNING):
            if self.run_one() is None:
                return

    def drain(self) -> list[int]:
        """Run the lane until nothing is claimable, and return the jobs run."""
        ran: list[int] = []
        while True:
            job_id = self.run_one()
            if job_id is None:
                return ran
            ran.append(job_id)

    def watch(self, source_id: int, *, origin: str = ORIGIN_MANUAL) -> tuple[int, Any]:
        """Queue one watch, run it, and return its job id and its checkpoint."""
        job_id = self.queue(source_id, origin=origin)
        self.run_until(job_id)
        return job_id, read_checkpoint(self.conn, job_id)

    def job(self, job_id: int) -> sqlite3.Row:
        """The job row, which the tests read their questions off."""
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row is not None
        return row

    def captures(self) -> list[sqlite3.Row]:
        """Every capture job, oldest first, whatever state it is in."""
        return list(
            self.conn.execute(
                "SELECT * FROM jobs WHERE kind = ? ORDER BY id", (CAPTURE_JOB_KIND,)
            ).fetchall()
        )

    def row(self, platform_video_id: str, source_id: int | None = None) -> Any:
        """The video row held for one platform id, or the one of one source."""
        found = videos_of_platform(self.conn, platform_video_id)
        if source_id is None:
            return found[0] if found else None
        return next((video for video in found if video.source_id == source_id), None)


def one_meeting_video() -> VideoListing:
    """One listing of a City Council regular session, as the feed gives it."""
    return listed("jhsFsEz0P5A", "City Council Regular Session - 22 September 2026")


# -- one new video, one capture ------------------------------------------------


def test_a_new_meeting_video_queues_exactly_one_capture(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """Spec 8.1: a listed meeting video becomes one capture job."""
    world.meeting(world.body_id, "2026-09-22T19:00:00", "City Council Regular Session")
    source = world.channel()
    watch = Watch(db_path, conn, ladder=ListingLadder(rss=StubLister([one_meeting_video()])))

    job_id, checkpoint = watch.watch(source)

    assert watch.job(job_id)["state"] == "done"
    assert checkpoint["method"] == SOURCE_RSS
    assert checkpoint["videos_listed"] == 1
    assert checkpoint["meeting_videos"] == 1
    assert checkpoint["other_videos"] == 0
    assert checkpoint["videos_created"] == 1
    assert checkpoint["videos_linked"] == 1
    assert checkpoint["captures_queued"] == 1

    queued = watch.captures()
    assert len(queued) == 1, "a meeting video is one capture job, not none and not two"
    assert queued[0]["lane"] == CAPTURE_LANE
    assert queued[0]["state"] == QUEUED
    assert queued[0]["origin"] == ORIGIN_MANUAL
    assert json.loads(queued[0]["payload"]) == {"video_id": watch.row("jhsFsEz0P5A").id}
    assert watch.capture_runs == [], "the capture was queued, not run by the watch"


def test_a_second_watch_of_a_captured_video_queues_no_second_capture(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """A video already captured is not captured again."""
    world.meeting(world.body_id, "2026-09-22T19:00:00", "City Council Regular Session")
    source = world.channel()
    watch = Watch(db_path, conn, ladder=ListingLadder(rss=StubLister([one_meeting_video()])))

    watch.watch(source)
    assert len(watch.captures()) == 1
    watch.drain()
    assert len(watch.capture_runs) == 1, "the child capture job ran"
    assert watch.row("jhsFsEz0P5A").capture_state == CAPTURED

    _, second = watch.watch(source)

    assert len(watch.captures()) == 1, "the second watch made no second job"
    assert second["captures_queued"] == 0
    assert second["already_captured"] == 1
    assert second["videos_created"] == 0
    assert second["videos_updated"] == 1


def test_a_second_watch_while_the_capture_still_waits_queues_no_second_capture(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """A capture that is still waiting is already queued, so it is not queued again.

    A feed carries no status, so its videos read as ``unknown`` and the real
    capture job defers on them (spec 8.2). The job stays in the queue with a
    later moment to run at, and the second watch has to see that and add
    nothing.
    """
    world.meeting(world.body_id, "2026-09-22T19:00:00", "City Council Regular Session")
    source = world.channel()
    watch = Watch(
        db_path, conn, ladder=ListingLadder(rss=StubLister([one_meeting_video()])), capture=WAITING
    )

    watch.watch(source)
    watch.drain()
    first = watch.captures()
    assert len(first) == 1
    assert first[0]["state"] == QUEUED, "a deferred capture is still queued, not finished"
    assert first[0]["run_after"] is not None, "it waits for a later moment"
    assert len(watch.capture_runs) == 1
    assert watch.row("jhsFsEz0P5A").capture_state == "pending"

    _, second = watch.watch(source)

    assert len(watch.captures()) == 1, "one waiting capture stays one"
    assert second["captures_queued"] == 0
    assert second["already_captured"] == 1
    assert any("already queued" in sentence for sentence in second["skipped"])


def test_an_upcoming_video_is_counted_and_not_captured(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """Spec 8.2: a video that has not happened yet is left for a later watch."""
    world.meeting(world.body_id, "2026-09-22T19:00:00", "City Council Regular Session")
    source = world.channel()
    upcoming = listed(
        "uHFuCfd5MVQ",
        "City Council Regular Session - 22 September 2026",
        method=SOURCE_YTDLP,
        live_broadcast_content="upcoming",
    )
    watch = Watch(db_path, conn, ladder=ListingLadder(ytdlp=StubLister([upcoming])))

    job_id, checkpoint = watch.watch(source)

    assert watch.job(job_id)["state"] == "done"
    assert watch.captures() == [], "an upcoming meeting is not captured early"
    assert watch.capture_runs == []
    assert checkpoint["meeting_videos"] == 1
    assert checkpoint["not_ready"] == 1
    assert checkpoint["captures_queued"] == 0
    assert "upcoming" in checkpoint["not_ready_videos"][0]
    assert "spec 8.2" in checkpoint["not_ready_videos"][0]
    # The row is stored, because a listing that saw it is a fact, and its capture
    # state is still pending: a later watch is what captures it.
    assert watch.row("uHFuCfd5MVQ").readiness == "upcoming"
    assert watch.row("uHFuCfd5MVQ").capture_state == "pending"


# -- a row is never renamed ----------------------------------------------------


def test_a_row_a_portal_sync_made_is_not_renamed(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """Spec 7.2 step 7: a row that exists is never renamed by another reader."""
    portal_id = world.source(type=portal.MEETING_PORTAL, origin="https://portal.test.invalid")
    world.meeting(world.body_id, "2026-09-22T19:00:00", "City Council Regular Session")
    theirs = insert_video(
        conn,
        source_id=portal_id,
        platform_video_id="jhsFsEz0P5A",
        title="City Council Regular Session - 22 September 2026",
        url="https://www.youtube.com/watch?v=jhsFsEz0P5A",
        published_at=A_PUBLISHED,
        duration_s=14290,
        capture_state="pending",
        source_note="Listed by the meeting portal source.",
    )
    source = world.channel()
    channel_listing = listed("jhsFsEz0P5A", "City Council Regular Session 9/22/26")
    watch = Watch(db_path, conn, ladder=ListingLadder(rss=StubLister([channel_listing])))

    watch.watch(source)

    kept = conn.execute("SELECT * FROM videos WHERE id = ?", (theirs,)).fetchone()
    assert kept["source_id"] == portal_id
    assert kept["title"] == "City Council Regular Session - 22 September 2026"
    assert kept["url"] == "https://www.youtube.com/watch?v=jhsFsEz0P5A"
    assert kept["duration_s"] == 14290
    assert kept["source_note"] == "Listed by the meeting portal source."

    mine = watch.row("jhsFsEz0P5A", source)
    assert mine is not None and mine.id != theirs, "the channel keeps its own row"
    assert mine.title == "City Council Regular Session 9/22/26"
    assert len(videos_of_platform(conn, "jhsFsEz0P5A")) == 2


def test_a_row_this_watch_made_is_not_renamed_by_a_later_listing(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """The channel's own row keeps the title a listing first gave it."""
    world.meeting(world.body_id, "2026-09-22T19:00:00", "City Council Regular Session")
    source = world.channel()
    watch = Watch(db_path, conn, ladder=ListingLadder(rss=StubLister([one_meeting_video()])))
    watch.watch(source)
    first = watch.row("jhsFsEz0P5A", source).id

    # The channel renames the video, as a channel really does.
    watch.ladder = ListingLadder(
        rss=StubLister([listed("jhsFsEz0P5A", "City Council Regular Session (renamed)")])
    )
    _, checkpoint = watch.watch(source)

    assert checkpoint["videos_created"] == 0
    assert checkpoint["videos_updated"] == 1
    kept = watch.row("jhsFsEz0P5A", source)
    assert kept.id == first, "the rename made no new row"
    assert kept.title == "City Council Regular Session - 22 September 2026", (
        "the title the row was made with is the title it keeps"
    )


# -- a zero is never a quiet day -----------------------------------------------


def test_a_listing_that_finds_nothing_names_every_method_it_tried(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """Spec 16.3: a zero says which readers were tried and what each answered."""
    source = world.channel()
    watch = Watch(db_path, conn, ladder=ListingLadder(rss=StubLister([])))

    job_id, checkpoint = watch.watch(source)

    assert watch.job(job_id)["state"] == "failed"
    assert checkpoint["videos_listed"] == 0
    assert checkpoint["method"] is None
    assert checkpoint["zero_videos"] == ZERO_VIDEOS_NOTE
    assert [row["method"] for row in checkpoint["methods_tried"]] == [
        SOURCE_DATA_API,
        SOURCE_RSS,
        SOURCE_YTDLP,
    ]
    assert all(row["reason"] for row in checkpoint["methods_tried"])
    assert all(row["ok"] is False for row in checkpoint["methods_tried"])
    assert checkpoint["methods_tried"][1]["label"] == "Read the public feed instead"

    said = watch.job(job_id)["last_error"]
    for method in (SOURCE_DATA_API, SOURCE_RSS, SOURCE_YTDLP):
        assert method in said, f"the failure does not name the {method} step"
    assert "the public feed listed 0 videos" in said
    assert "has now failed 1 time(s) in a row" in said
    failures = conn.execute("SELECT consecutive_failures FROM sources").fetchone()
    assert failures["consecutive_failures"] == 1


def test_a_watch_that_listed_nothing_queues_nothing(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """A failed watch is a failed watch: no rows and no capture jobs."""
    source = world.channel()
    watch = Watch(db_path, conn, ladder=ListingLadder(rss=StubLister([])))

    watch.watch(source)

    assert watch.captures() == []
    assert conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0] == 0


# -- the source's own words (rule D) -------------------------------------------


def test_the_skip_seeds_come_from_the_source_itself(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """Rule D: one city's words are that source's settings, not everyone's.

    The same title is a meeting under the built-in seeds and not a meeting under
    a source configured to skip it, which is what makes the setting a setting
    rather than a constant of the code.
    """
    world.meeting(world.body_id, "2026-09-22T19:00:00", "City Council Regular Session")
    title = "City Council Regular Session - 22 September 2026"
    assert MeetingClassifier.from_settings({}).verdict(title).is_meeting, (
        "the built-in seeds are what the difference is measured against"
    )

    built_in = world.channel()
    configured = world.channel(
        origin=OTHER_CHANNEL_URL,
        settings=json.dumps({"meeting_skip_titles": ["City Council Regular Session"]}),
    )
    watch = Watch(db_path, conn, ladder=ListingLadder(rss=StubLister([one_meeting_video()])))

    _, first = watch.watch(built_in)
    _, second = watch.watch(configured)

    assert first["meeting_videos"] == 1
    assert first["captures_queued"] == 1
    assert second["meeting_videos"] == 0, "the source's own skip seeds decided this"
    assert second["other_videos"] == 1
    assert second["captures_queued"] == 0
    assert watch.row("jhsFsEz0P5A", configured) is not None, "the row is stored either way"


# -- the schedule (spec 16.2) --------------------------------------------------


def a_scheduler(db_path: Path, registry: Registry) -> Scheduler:
    """A real daily schedule over this test's database.

    No test video is set, so the yt-dlp update check of spec 8.9 is a paused run
    and the only job this schedule enqueues is the day's watch.
    """
    return Scheduler(
        db_path=db_path,
        registry=registry,
        time_zone=DENVER,
        daily_time=DAILY_TIME,
        test_video_url="",
        clock=lambda: A_MORNING,
        interval_s=0.05,
    )


def test_a_watch_the_schedule_asked_for_queues_a_capture_the_schedule_asked_for(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """The origin of a scheduled job's children is ``scheduled``, not ``manual``.

    A capture job a watch made carries the origin of the watch that made it, so
    a nightly watch's captures read as scheduled work in the queue rather than
    as something a person asked for.
    """
    world.meeting(world.body_id, "2026-09-22T19:00:00", "City Council Regular Session")
    source = world.channel()
    watch = Watch(db_path, conn, ladder=ListingLadder(rss=StubLister([one_meeting_video()])))
    scheduler = a_scheduler(db_path, watch.registry)

    enqueued = scheduler.tick()
    assert len(enqueued) == 1, "one accepted channel source, one watch"
    parent = watch.job(enqueued[0])
    assert parent["kind"] == TASK_WATCH
    assert parent["origin"] == ORIGIN_SCHEDULED
    assert json.loads(parent["payload"]) == {"source_id": source}
    assert parent["state"] == QUEUED

    watch.run_until(enqueued[0])

    captures = watch.captures()
    assert len(captures) == 1
    assert captures[0]["origin"] == ORIGIN_SCHEDULED, "the child carries its parent's origin"
    assert watch.job(enqueued[0])["state"] == "done"


def test_a_watch_a_person_asked_for_queues_a_manual_capture(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """The other origin, so the check above is about the origin and not a default."""
    world.meeting(world.body_id, "2026-09-22T19:00:00", "City Council Regular Session")
    source = world.channel()
    watch = Watch(db_path, conn, ladder=ListingLadder(rss=StubLister([one_meeting_video()])))

    watch.watch(source)

    assert watch.captures()[0]["origin"] == ORIGIN_MANUAL


def test_a_channel_source_whose_origin_names_no_channel_pauses_the_day_it_is_owed(
    db_path: Path, conn: sqlite3.Connection, world: World
) -> None:
    """A channel address a watch cannot read is a paused run with the reason.

    The sentence is the watch's own refusal rather than the portal's, because
    the two kinds of source are read in different ways (rule D). A source with
    no origin at all cannot be stored -- migration 0005 requires an origin of at
    least one character -- so the reachable refusal is an origin that is not a
    channel.
    """
    source = world.channel(origin="https://example.test/not-a-channel")
    watch = Watch(db_path, conn, ladder=ListingLadder(rss=StubLister([one_meeting_video()])))
    scheduler = a_scheduler(db_path, watch.registry)

    assert scheduler.tick() == (), "nothing was enqueued for the day"
    paused = conn.execute("SELECT * FROM schedule_runs WHERE task = ?", (TASK_WATCH,)).fetchone()
    assert paused is not None
    assert paused["state"] == "paused"
    assert paused["job_id"] is None
    assert paused["subject"] == f"source:{source}"
    assert "not a YouTube channel this watch can read" in paused["reason"]
    assert "meeting portal with no origin" not in paused["reason"], (
        "the portal's sentence is about a portal"
    )
    assert "TOWNRECORD_TIME_ZONE" not in paused["reason"], "the zone was configured"


# -- the recorded City of Longmont feed ----------------------------------------


def city_ladder() -> ListingLadder:
    """The real feed reader over the recorded City of Longmont feed bytes."""
    body = CITY_RSS.read_bytes()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/feeds/videos.xml":
            return httpx.Response(200, content=body, headers={"content-type": "text/xml"})
        return httpx.Response(404, text=f"the recorded feed has no {request.url.path}")

    return ListingLadder(rss=RssLister(offline_client(transport=httpx.MockTransport(answer))))


@needs(CITY_RSS)
def test_the_recorded_city_feed_reads_as_the_feed_it_is(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    """The fixture is what the tests below assume it is: 15 entries, no status."""
    result = city_ladder().list_videos(channel_id=CITY_CHANNEL_ID)

    assert result.method == SOURCE_RSS
    assert result.label == "Read the public feed instead"
    assert len(result.videos) == 15
    assert len(result.attempts) == 2, "the API step refused first, then the feed answered"
    assert result.attempts[0].method == SOURCE_DATA_API
    assert result.attempts[0].ok is False
    assert all(video.readiness() == "unknown" for video in result.videos)
    assert all(video.channel_id == CITY_CHANNEL_ID for video in result.videos)


@needs(CITY_RSS)
def test_the_recorded_city_feed_becomes_rows_and_captures(
    db_path: Path, conn: sqlite3.Connection
) -> None:
    """What one watch of the recorded City feed does, counted.

    A city of bodies and meetings is set up first, so the videos that name one
    of them are linked and the rest are recorded with a plain note.
    """
    city = World(conn, name="City of Longmont")
    council = city.body_id
    zoning = city.body("Planning and Zoning Commission")
    city.meeting(council, "2026-09-22T19:00:00", "City Council Regular Session")
    city.meeting(council, "2026-09-15T19:00:00", "City Council Study Session", type="study")
    city.meeting(zoning, "2026-09-23T19:00:00", "Planning and Zoning Commission")
    city.meeting(zoning, "2026-09-16T19:00:00", "Planning and Zoning Commission")
    source = city.channel()
    watch = Watch(db_path, conn, ladder=city_ladder())

    job_id, checkpoint = watch.watch(source)

    assert watch.job(job_id)["state"] == "done"
    assert checkpoint["videos_listed"] == 15
    assert checkpoint["meeting_videos"] == 8
    assert checkpoint["other_videos"] == 7
    assert checkpoint["videos_created"] == 15
    assert checkpoint["videos_linked"] == 4
    assert checkpoint["captures_queued"] == 8
    assert checkpoint["not_ready"] == 0, "a feed carries no status, so none is upcoming or live"

    captures = watch.captures()
    assert len(captures) == 8
    assert {row["origin"] for row in captures} == {ORIGIN_MANUAL}
    queued_videos = [int(json.loads(row["payload"])["video_id"]) for row in captures]
    assert len(set(queued_videos)) == 8, "each meeting video is queued once"
    assert sum(get_video(conn, video_id).meeting_id is not None for video_id in queued_videos) == 4

    linked = [
        row["platform_video_id"]
        for row in conn.execute(
            "SELECT platform_video_id FROM videos WHERE meeting_id IS NOT NULL"
        ).fetchall()
    ]
    assert len(linked) == 4, "four of the eight meeting videos name a body and a meeting"
    assert len(set(linked)) == 4

    # A meeting video that names a body this city does not hold is stored with
    # its reason, never filed under a body that is not its own.
    assert checkpoint["skipped_total"] == 4
    assert all("names no body of jurisdiction" in sentence for sentence in checkpoint["skipped"]), (
        checkpoint["skipped"]
    )
