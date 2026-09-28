"""The caption recheck job, and the rules that settle a capture (spec 8.7).

Spec 8.7 makes a capture provisional for 24 hours after the meeting ends and
rechecked every 3 hours in that time; two unchanged checks at least an hour apart
settle it; 48 hours after the first capture the backstop settles it whatever the
source did; and a revision raises one review item and never rewrites what was
already exported. This file tests those rules twice over: once against the
arithmetic of :mod:`townrecord.capture.settle`, and once end to end through the
real job with the fake yt-dlp of :mod:`tests.capture.fakes`.

No test here reaches YouTube and none waits on a schedule: the clock is the
test's own, and every fetch is a fake that writes the files yt-dlp would have
written (PROJECT-BRIEF rules 4 and 11b).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from townrecord import pacing, repo
from townrecord.capture import (
    CaptionCapture,
    CaptionRecheck,
    CaptureSettings,
    next_recheck_at,
)
from townrecord.capture.job import PACE_WHAT as CAPTURE_PACE_WHAT
from townrecord.capture.requests import RECHECK_JOB_KIND, RECHECK_PACE_WHAT
from townrecord.capture.settle import (
    CHANGE_DURATION,
    CHANGE_HASH,
    CHANGE_ORDER,
    CHANGE_REVISION_AT,
    FORCED_SETTLE_AFTER,
    RECHECK_EVERY,
    SETTLED,
    SETTLED_UNDER_CHURN,
    Check,
    Observation,
    decide,
    meeting_end,
    state_of,
    unchanged_pair,
)
from townrecord.config import Settings
from townrecord.jobs import (
    DONE,
    ORIGIN_MANUAL,
    ORIGIN_SCHEDULED,
    QUEUED,
    JobContext,
    JobDeferred,
    claim,
    finish,
    get,
)
from townrecord.jobs.queue import stamp
from townrecord.records.portal import ALIGN_MEETING
from townrecord.status import collect, render

from .fakes import FIXTURE_SRV3, PLATFORM_VIDEO_ID, FakeClock, FakeYtDlp, claimed_job, fake_429

#: The moment the first capture of these tests is dated, and the moment the
#: meeting it belongs to ended. Both are the test's, because every rule of spec
#: 8.7 is a statement about hours and the database clock would date a capture
#: today. The capture is an hour after the meeting ended: provisional, and not
#: the "more than 24 hours" shortcut of the first rule.
CAPTURED = datetime(2026, 9, 9, 4, 0, tzinfo=UTC)
MEETING_ENDED = datetime(2026, 9, 9, 3, 0, tzinfo=UTC)

#: How long the video the fixtures capture runs, from the sidecar of
#: :data:`tests.capture.fakes.INFO_AUTO_CAPTIONS`.
LENGTH_S = 7200.0

#: The captions as the source gives them the second time: the same track with one
#: word spoken differently. A revision is new bytes, and new bytes are the first
#: signal the change test reads, so this is enough of a revision to test.
REVISED_SRV3 = FIXTURE_SRV3.read_bytes().replace(b">I</s>", b">We</s>", 1)


# -- helpers ------------------------------------------------------------------


def a_check(minutes: float, *, changed: bool = False) -> Check:
    """One check of :data:`CAPTURED`'s capture, `minutes` after it was taken."""
    return Check(checked_at=CAPTURED + timedelta(minutes=minutes), changed=changed)


def capture_video(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    clock: FakeClock,
    *,
    caption: bytes | None = None,
    origin: str = ORIGIN_MANUAL,
) -> FakeYtDlp:
    """Capture the video once, for real, and close the job.

    Every test here starts from a real capture rather than from rows written by
    hand: the capture is what dates the transcript (the 48 hour backstop and the
    24 hour window are both counted from it), what stores the caption version
    the checks are compared against, and what queues the first recheck.

    ``origin`` is how the capture was asked for, so that the tests about what a
    capture hands down can start from a scheduled one (spec 16.2).
    """
    fake = FakeYtDlp()
    if caption is not None:
        fake.caption = caption
    ctx = claimed_job(conn, video, clock, origin=origin)
    CaptionCapture(
        storage_root=storage_root, settings=CaptureSettings(), runner=fake, interpreter="python"
    )(ctx)
    finish(conn, ctx.job_id, ctx.claim_token, DONE, clock=clock)
    return fake


def claim_until(
    conn: sqlite3.Connection, clock: FakeClock, kind: str, *, lane: str = "normal"
) -> JobContext:
    """Claim the oldest queued job of `kind`, closing anything that is not it.

    A capture queues an alignment job (spec 16.3) and its own first recheck into
    this same lane, so a plain claim would reach the wrong row. The helper the
    capture tests use closes what is not asked for, for the same reason.

    The context carries the claimed row's own origin (spec 16.2), which is what
    a handler reads: a helper that left it at the default would hand every job a
    ``manual`` context and hide whether an origin was passed on.
    """
    taken = claim(conn, lane, "worker-1", clock=clock)
    while taken is not None and taken.kind != kind:
        finish(conn, taken.job_id, taken.token, DONE, clock=clock)
        taken = claim(conn, lane, "worker-1", clock=clock)
    assert taken is not None, f"no {kind} job was queued"
    return JobContext(
        conn=conn,
        job_id=taken.job_id,
        kind=taken.kind,
        payload=taken.payload,
        lane=taken.lane,
        claim_token=taken.token,
        clock=clock,
        origin=taken.origin,
    )


def say_captured_at(conn: sqlite3.Connection, video: int, when: datetime) -> None:
    """Date the capture as the test says.

    ``insert_transcript_with_segments`` writes the database's own clock and
    takes no moment, so the 24 hour window and the 48 hour backstop -- both
    counted from the first capture -- are set here instead.
    """
    conn.execute("UPDATE transcripts SET created_at = ? WHERE video_id = ?", (stamp(when), video))


def say_meeting_ended(conn: sqlite3.Connection, meeting: int, when: datetime) -> None:
    """Say when the meeting ended, and how long it ran.

    A meeting carries a start and no end (spec 6.2), so its end is stated by
    moving the start back by the length and giving the video that length. The
    ``video`` fixture states no length at all, and without one no meeting end can
    be known and the first rule of spec 8.7 can never fire.
    """
    conn.execute(
        "UPDATE meetings SET starts_at = ? WHERE id = ?",
        (stamp(when - timedelta(seconds=LENGTH_S)), meeting),
    )
    conn.execute("UPDATE videos SET duration_s = ? WHERE meeting_id = ?", (LENGTH_S, meeting))


def stored_sha(conn: sqlite3.Connection, transcript_id: int) -> str:
    """The hash of the caption bytes one transcript version was stored from."""
    row = conn.execute(
        "SELECT a.sha256 AS sha256 FROM artifacts a JOIN transcripts t ON t.artifact_id = a.id "
        "WHERE t.id = ?",
        (transcript_id,),
    ).fetchone()
    assert row is not None, "the transcript has no artifact"
    return str(row["sha256"])


def recheck_at(
    conn: sqlite3.Connection,
    storage_root: Path,
    clock: FakeClock,
    at: datetime,
    *,
    caption: bytes | None = None,
) -> tuple[str | None, FakeYtDlp]:
    """Move the clock to `at`, run one recheck, and say how it ended.

    Returns the reason the job deferred with -- None when the capture settled and
    the job was done -- and the fake it fetched through. A deferral is not a
    failure: the job goes back on the queue with a sentence for its reason, and
    that sentence is what a user reads.
    """
    clock.now = at
    fake = FakeYtDlp()
    if caption is not None:
        fake.caption = caption
    handler = CaptionRecheck(
        storage_root=storage_root, settings=CaptureSettings(), runner=fake, interpreter="python"
    )
    ctx = claim_until(conn, clock, RECHECK_JOB_KIND)
    try:
        handler(ctx)
    except JobDeferred as deferred:
        return str(deferred), fake
    finish(conn, ctx.job_id, ctx.claim_token, DONE, clock=clock)
    return None, fake


def checks(conn: sqlite3.Connection, video: int) -> list[repo.CaptionCheck]:
    return repo.checks_of(conn, video)


def transcripts(conn: sqlite3.Connection, video: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM transcripts WHERE video_id = ? ORDER BY id", (video,)
        ).fetchall()
    )


def align_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every align job, oldest first (spec 16.3)."""
    return list(
        conn.execute("SELECT * FROM jobs WHERE kind = ? ORDER BY id", (ALIGN_MEETING,)).fetchall()
    )


def version_in_hand(conn: sqlite3.Connection, video: int) -> sqlite3.Row:
    """The transcript version the rest of the system reads (spec 8.7)."""
    row = conn.execute(
        "SELECT * FROM transcripts WHERE video_id = ? ORDER BY is_provisional, id DESC LIMIT 1",
        (video,),
    ).fetchone()
    assert row is not None, "the video has no transcript"
    return row


def ready_for_recheck(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    meeting: int,
    *,
    origin: str = ORIGIN_MANUAL,
) -> FakeClock:
    """Capture the fixture video, date it, and say when its meeting ended."""
    clock = FakeClock(start=CAPTURED)
    capture_video(conn, storage_root, video, clock, origin=origin)
    say_captured_at(conn, video, CAPTURED)
    say_meeting_ended(conn, meeting, MEETING_ENDED)
    return clock


# -- the rules, against the arithmetic (spec 8.7) -----------------------------


def test_two_unchanged_checks_under_an_hour_apart_do_not_settle() -> None:
    """Spec 8.7 wants two checks at least an hour apart, and 45 minutes is not two."""
    pair = [a_check(0), a_check(45)]
    decision = decide(
        now=CAPTURED + timedelta(minutes=45),
        captured_at=CAPTURED,
        meeting_ended_at=MEETING_ENDED,
        checks=pair,
    )

    assert unchanged_pair(pair) is False
    assert decision.settle is False
    # And the capture is looked at again three hours after the check just taken.
    assert decision.next_check_at == CAPTURED + timedelta(minutes=45) + RECHECK_EVERY
    assert state_of(is_provisional=True, settled_under_churn=False) == "provisional"


def test_two_unchanged_checks_an_hour_apart_settle() -> None:
    """The same two answers, an hour apart, are the whole of the second rule."""
    pair = [a_check(0), a_check(60)]
    decision = decide(
        now=CAPTURED + timedelta(hours=1),
        captured_at=CAPTURED,
        meeting_ended_at=MEETING_ENDED,
        checks=pair,
    )

    assert unchanged_pair(pair) is True
    assert decision.settle is True
    assert decision.under_churn is False
    assert decision.next_check_at is None
    assert state_of(is_provisional=False, settled_under_churn=decision.under_churn) == SETTLED


def test_one_check_is_not_two_however_long_it_has_been_since() -> None:
    """Spec 8.7 settles on a pair, so a lone check leaves the capture provisional."""
    decision = decide(
        now=CAPTURED + timedelta(hours=30),
        captured_at=CAPTURED,
        meeting_ended_at=None,
        checks=[a_check(0)],
    )

    assert decision.settle is False


def test_a_capture_made_more_than_a_day_after_the_meeting_is_final_at_once() -> None:
    """The first rule of spec 8.7: a late capture has nothing left to wait for."""
    late = MEETING_ENDED + timedelta(hours=25)
    decision = decide(now=late, captured_at=late, meeting_ended_at=MEETING_ENDED, checks=[])

    assert decision.settle is True
    assert decision.under_churn is False

    # Exactly 24 hours is not "more than 24 hours", and that capture waits.
    on_the_line = MEETING_ENDED + timedelta(hours=24)
    waiting = decide(
        now=on_the_line, captured_at=on_the_line, meeting_ended_at=MEETING_ENDED, checks=[]
    )
    assert waiting.settle is False
    assert waiting.next_check_at == on_the_line + RECHECK_EVERY

    # A meeting whose end nobody knows is never more than 24 hours ago.
    unknown = decide(now=late, captured_at=late, meeting_ended_at=None, checks=[])
    assert unknown.settle is False


def test_the_backstop_settles_under_churn_when_a_check_found_a_change() -> None:
    """Spec 8.7: 48 hours after the first capture it is settled anyway, and the word changes."""
    churned = [a_check(0), a_check(60, changed=True)]
    decision = decide(
        now=CAPTURED + FORCED_SETTLE_AFTER,
        captured_at=CAPTURED,
        meeting_ended_at=MEETING_ENDED,
        checks=churned,
    )

    assert decision.settle is True
    assert decision.under_churn is True
    assert state_of(is_provisional=False, settled_under_churn=decision.under_churn) == (
        SETTLED_UNDER_CHURN
    )

    # The backstop of a capture that never changed is an ordinary settling, and a
    # capture a minute inside the 48 hours is not settled at all: the backstop is
    # a moment, not a habit.
    quiet = decide(
        now=CAPTURED + FORCED_SETTLE_AFTER,
        captured_at=CAPTURED,
        meeting_ended_at=MEETING_ENDED,
        checks=[a_check(0)],
    )
    assert (quiet.settle, quiet.under_churn) == (True, False)

    early = decide(
        now=CAPTURED + FORCED_SETTLE_AFTER - timedelta(minutes=1),
        captured_at=CAPTURED,
        meeting_ended_at=MEETING_ENDED,
        checks=[a_check(0)],
    )
    assert early.settle is False


def test_the_change_test_reads_the_hash_then_the_revision_time_then_the_length() -> None:
    """Spec 8.7 fixes the order, and a signal is read only when the ones before it agree."""
    earlier = Observation(sha256="a" * 64, revision_at="2026-09-08T19:00:00Z", duration_s=LENGTH_S)

    assert CHANGE_ORDER == (CHANGE_HASH, CHANGE_REVISION_AT, CHANGE_DURATION)
    assert (
        Observation(sha256="b" * 64, revision_at="2026-09-09T19:00:00Z", duration_s=3600.0)
    ).differs_from(earlier) == CHANGE_HASH
    assert (
        Observation(sha256="a" * 64, revision_at="2026-09-09T19:00:00Z", duration_s=3600.0)
    ).differs_from(earlier) == CHANGE_REVISION_AT
    assert (
        Observation(sha256="a" * 64, revision_at="2026-09-08T19:00:00Z", duration_s=3600.0)
    ).differs_from(earlier) == CHANGE_DURATION
    assert (
        Observation(sha256="a" * 64, revision_at="2026-09-08T19:00:00Z", duration_s=LENGTH_S)
    ).differs_from(earlier) is None

    # A signal neither side states is not a change: saying nothing twice is
    # agreeing, and a source that states nothing is never the one that changed.
    assert Observation().differs_from(Observation()) is None


def test_the_meeting_end_is_the_start_plus_the_length_of_the_video() -> None:
    """The portal's listing carries no end (spec 6.2), so the length gives it."""
    assert meeting_end("2026-09-08T19:00:00-06:00", LENGTH_S) == MEETING_ENDED
    assert meeting_end("2026-09-08T19:00:00-06:00", None) is None
    assert meeting_end("2026-09-08T19:00:00-06:00", 0) is None
    assert meeting_end(None, LENGTH_S) is None


# -- the job, end to end ------------------------------------------------------


def test_two_rechecks_under_an_hour_apart_leave_the_capture_provisional(
    conn: sqlite3.Connection, storage_root: Path, video: int, meeting: int
) -> None:
    """Two unchanged checks 45 minutes apart are on record, and the job checks again."""
    clock = ready_for_recheck(conn, storage_root, video, meeting)
    held = version_in_hand(conn, video)
    sha = stored_sha(conn, int(held["id"]))
    for at in (CAPTURED + timedelta(minutes=10), CAPTURED + timedelta(minutes=55)):
        repo.insert_check(
            conn,
            video_id=video,
            transcript_id=int(held["id"]),
            checked_at=stamp(at),
            observed_sha256=sha,
            observed_duration_s=LENGTH_S,
        )

    reason, fake = recheck_at(conn, storage_root, clock, CAPTURED + timedelta(minutes=55))

    assert reason is not None, "the capture settled on two checks 45 minutes apart"
    assert len(fake.capture_calls) == 1, "the recheck did not read the source again"
    assert len(checks(conn, video)) == 3
    assert version_in_hand(conn, video)["is_provisional"] == 1


def test_two_rechecks_an_hour_apart_settle_the_capture(
    conn: sqlite3.Connection, storage_root: Path, video: int, meeting: int
) -> None:
    """The pair spec 8.7 settles on, and no fetch at all once it is settled."""
    clock = ready_for_recheck(conn, storage_root, video, meeting)
    held = version_in_hand(conn, video)
    sha = stored_sha(conn, int(held["id"]))
    for at in (CAPTURED + timedelta(hours=1), CAPTURED + timedelta(hours=2)):
        repo.insert_check(
            conn,
            video_id=video,
            transcript_id=int(held["id"]),
            checked_at=stamp(at),
            observed_sha256=sha,
            observed_duration_s=LENGTH_S,
        )

    reason, fake = recheck_at(conn, storage_root, clock, CAPTURED + timedelta(hours=2))

    assert reason is None
    assert fake.capture_calls == [], "a settled capture was fetched again"
    assert len(checks(conn, video)) == 2, "a settled capture wrote a check"
    settled = version_in_hand(conn, video)
    assert settled["is_provisional"] == 0
    assert settled["settled_under_churn"] == 0
    assert settled["settled_at"] == stamp(CAPTURED + timedelta(hours=2))
    assert next_recheck_at(conn, video) is None, "a recheck is still waiting"


def test_a_capture_made_more_than_a_day_after_the_meeting_settles_without_fetching(
    conn: sqlite3.Connection, storage_root: Path, video: int, meeting: int
) -> None:
    """Spec 8.7's first rule, through the job: nothing is read, and it is final."""
    at = MEETING_ENDED + timedelta(hours=25)
    clock = ready_for_recheck(conn, storage_root, video, meeting)
    say_captured_at(conn, video, at)

    reason, fake = recheck_at(conn, storage_root, clock, at)

    assert reason is None
    assert fake.capture_calls == []
    assert checks(conn, video) == []
    settled = version_in_hand(conn, video)
    assert (settled["is_provisional"], settled["settled_under_churn"]) == (0, 0)


def test_a_capture_exactly_a_day_after_the_meeting_is_still_rechecked(
    conn: sqlite3.Connection, storage_root: Path, video: int, meeting: int
) -> None:
    """The word is "more than 24 hours", and at exactly 24 the capture waits."""
    at = MEETING_ENDED + timedelta(hours=24)
    clock = ready_for_recheck(conn, storage_root, video, meeting)
    say_captured_at(conn, video, at)

    reason, fake = recheck_at(conn, storage_root, clock, at)

    assert reason is not None
    assert len(fake.capture_calls) == 1
    assert version_in_hand(conn, video)["is_provisional"] == 1


def test_the_backstop_settles_a_capture_that_kept_changing_under_churn(
    conn: sqlite3.Connection, storage_root: Path, video: int, meeting: int
) -> None:
    """48 hours after the first capture, a source that never stopped is settled anyway."""
    clock = ready_for_recheck(conn, storage_root, video, meeting)

    # An hour in, the captions are what they were, and the job defers.
    first, quiet = recheck_at(conn, storage_root, clock, CAPTURED + timedelta(hours=1))
    assert first is not None
    assert len(quiet.capture_calls) == 1

    # Four hours in, they have changed: one revision, and still provisional.
    second, revised = recheck_at(
        conn, storage_root, clock, CAPTURED + timedelta(hours=4), caption=REVISED_SRV3
    )
    assert second is not None
    assert len(revised.capture_calls) == 1
    assert version_in_hand(conn, video)["is_provisional"] == 1

    # The deferral follows the 3 hour cadence: three hours past the check just
    # taken, which is well inside the window. It is the backstop that ends the
    # watching, and the next claim of this job is made at the 48 hour mark,
    # where the job settles it before reading anything.
    assert next_recheck_at(conn, video) == stamp(CAPTURED + timedelta(hours=7))

    # At 48 hours the backstop settles it, without reading the source again.
    end, last = recheck_at(conn, storage_root, clock, CAPTURED + FORCED_SETTLE_AFTER)

    assert end is None
    assert last.capture_calls == []
    assert [check.changed for check in checks(conn, video)] == [False, True]

    held = version_in_hand(conn, video)
    assert held["is_provisional"] == 0
    assert held["settled_under_churn"] == 1
    assert state_of(
        is_provisional=False, settled_under_churn=bool(held["settled_under_churn"])
    ) == (SETTLED_UNDER_CHURN)
    # Nothing is waiting: the job that settled it was the last one.
    assert next_recheck_at(conn, video) is None


def test_a_changed_hash_raises_one_review_item_and_keeps_the_old_version(
    conn: sqlite3.Connection, storage_root: Path, video: int, meeting: int
) -> None:
    """Spec 8.7: a revision is a new version, one item, and nothing rewritten."""
    assert FIXTURE_SRV3.read_bytes() != REVISED_SRV3, "the revision fixture is not a revision"
    clock = ready_for_recheck(conn, storage_root, video, meeting)

    first, quiet = recheck_at(conn, storage_root, clock, CAPTURED + timedelta(hours=1))
    assert first is not None
    assert len(quiet.capture_calls) == 1
    before = transcripts(conn, video)
    assert len(before) == 1
    old = before[0]
    old_segments = conn.execute(
        "SELECT COUNT(*) AS count FROM segments WHERE transcript_id = ?", (old["id"],)
    ).fetchone()["count"]
    assert old_segments > 0

    # The alignment of this meeting was made from that older version, so a
    # revision has to mark it rather than change it.
    alignment = repo.save_meeting_alignment(conn, meeting_id=meeting, agenda_item_count=3)

    at = CAPTURED + timedelta(hours=4)
    reason, fake = recheck_at(conn, storage_root, clock, at, caption=REVISED_SRV3)

    assert reason is not None
    assert len(fake.capture_calls) == 1
    row = checks(conn, video)[-1]
    assert (row.changed, row.what_changed) == (True, CHANGE_HASH)

    items = repo.review_items(conn, video_id=video)
    assert len(items) == 1, "a revision raised more than one review item"
    assert items[0].kind == repo.REVISION_ITEM
    assert PLATFORM_VIDEO_ID in items[0].sentence
    # "When" is when the change was found -- the check at the fourth hour of the
    # capture's life -- and not when the capture was taken.
    assert "2026-09-09 08:00 UTC" in items[0].sentence
    assert "the caption file is not the bytes it was" in items[0].sentence
    assert "kept" in items[0].sentence

    # Two versions, the older one still whole: same row, same bytes, same hash,
    # same segments, and no longer the version in hand.
    after = transcripts(conn, video)
    assert len(after) == 2
    assert after[0]["id"] == old["id"]
    assert stored_sha(conn, int(old["id"])) == stored_sha(conn, int(after[0]["id"]))
    assert (
        conn.execute(
            "SELECT COUNT(*) AS count FROM segments WHERE transcript_id = ?", (old["id"],)
        ).fetchone()["count"]
        == old_segments
    )
    assert version_in_hand(conn, video)["id"] == after[1]["id"]

    # The alignment made from the older version is marked, not rewritten.
    marks = repo.rerun_marks_of(conn, meeting)
    assert [(mark.kind, mark.row_id) for mark in marks] == [(repo.RERUN_ALIGNMENT, alignment)]
    assert PLATFORM_VIDEO_ID in marks[0].reason

    # And a check that reads the same revised bytes again finds nothing new: one
    # item, one new version, and no second item for a change already raised.
    again, _later = recheck_at(
        conn, storage_root, clock, CAPTURED + timedelta(hours=7), caption=REVISED_SRV3
    )
    assert again is not None
    assert len(repo.review_items(conn, video_id=video)) == 1
    assert len(transcripts(conn, video)) == 2
    assert checks(conn, video)[-1].changed is False


def test_the_alignment_a_revision_asks_for_takes_after_the_recheck(
    conn: sqlite3.Connection, storage_root: Path, video: int, meeting: int
) -> None:
    """A revision's alignment is the child of the recheck that found it.

    The recheck of a scheduled capture is itself scheduled, so the alignment it
    asks for runs again for the same reason the recheck did (spec 16.2). The
    capture's own alignment job is closed by the first claim below, so the row
    that is read here is the one the revision writes.
    """
    clock = ready_for_recheck(conn, storage_root, video, meeting, origin=ORIGIN_SCHEDULED)

    recheck_at(conn, storage_root, clock, CAPTURED + timedelta(hours=1))
    assert [row["state"] for row in align_jobs(conn)] == [DONE]

    recheck_at(conn, storage_root, clock, CAPTURED + timedelta(hours=4), caption=REVISED_SRV3)

    rows = align_jobs(conn)
    assert len(rows) == 2, "the revision asked for the alignment of its meeting again"
    assert rows[-1]["origin"] == ORIGIN_SCHEDULED, "the alignment took after the recheck"


def test_a_rate_limited_recheck_defers_the_job_and_never_sleeps(
    conn: sqlite3.Connection, storage_root: Path, video: int, meeting: int, pace
) -> None:
    """Spec 8.2, 8.3, 16.1: a 429 hands the job back, and the pace holds, not sleeps."""
    clock = ready_for_recheck(conn, storage_root, video, meeting)
    fake = fake_429()
    handler = CaptionRecheck(
        storage_root=storage_root, settings=CaptureSettings(), runner=fake, interpreter="python"
    )
    ctx = claim_until(conn, clock, RECHECK_JOB_KIND)

    with pytest.raises(JobDeferred) as deferred:
        handler(ctx)
    reason = str(deferred.value)

    row = get(conn, ctx.job_id)
    assert row["state"] == QUEUED
    assert row["claim_token"] is None
    assert row["run_after"] == stamp(clock.now + timedelta(seconds=900))
    assert "rate limited" in reason
    assert "429" in reason

    # Nothing was written from a fetch that never answered, and the hold before
    # the next look was taken through the process pace (spec 8.3, 8.10).
    assert checks(conn, video) == []
    # Both YouTube calls of this test reserved their turn -- the capture of the
    # fixture, and this recheck -- and the reservation is a wait, not a sleep.
    assert pace.waits == [CAPTURE_PACE_WHAT, RECHECK_PACE_WHAT]
    assert pace.holds == [(RECHECK_PACE_WHAT, 900.0)]
    # The one sleep is the ordinary gap between two YouTube calls, which is the
    # pace doing its job. The 900 second hold the 429 set is not slept: it is
    # handed back to the queue, which is what "a wait defers" means (spec 16.1).
    assert pace.clock.slept == [pacing.DEFAULT_MINIMUM_GAP_S]
    assert next_recheck_at(conn, video) == stamp(clock.now + timedelta(seconds=900))


def test_a_capture_queues_its_own_first_recheck_and_it_says_when_it_is_due(
    conn: sqlite3.Connection, storage_root: Path, video: int, meeting: int
) -> None:
    """The queue is where the next recheck time lives (spec 16.1, 8.7)."""
    clock = ready_for_recheck(conn, storage_root, video, meeting)

    assert next_recheck_at(conn, video) is not None

    reason, _fake = recheck_at(conn, storage_root, clock, CAPTURED + timedelta(hours=1))
    assert reason is not None

    # The next time is the check just taken plus the 3 hour cadence of spec 8.7.
    assert next_recheck_at(conn, video) == stamp(CAPTURED + timedelta(hours=1) + RECHECK_EVERY)


# -- what a user is shown (spec 8.7, 16.2, 16.3) ------------------------------


def test_the_api_says_which_state_a_meeting_s_captions_are_in(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    meeting: int,
    body: int,
    client: TestClient,
    read_token: str,
) -> None:
    """Spec 16.2: per meeting, the state word and the next recheck time."""
    clock = ready_for_recheck(conn, storage_root, video, meeting)
    _reason, _fake = recheck_at(conn, storage_root, clock, CAPTURED + timedelta(hours=1))
    headers = {"Authorization": f"Bearer {read_token}"}
    url = f"/v1/bodies/{body}/meetings"

    provisional = client.get(url, headers=headers).json()[0]["videos"][0]
    assert provisional["caption_state"] == "provisional"
    assert provisional["next_recheck_at"] == stamp(CAPTURED + timedelta(hours=1) + RECHECK_EVERY)

    # A revision inside the window, and then the backstop at 48 hours.
    _reason, _fake = recheck_at(
        conn, storage_root, clock, CAPTURED + timedelta(hours=4), caption=REVISED_SRV3
    )
    end, _fake = recheck_at(conn, storage_root, clock, CAPTURED + FORCED_SETTLE_AFTER)
    assert end is None

    churned = client.get(url, headers=headers).json()[0]["videos"][0]
    assert churned["caption_state"] == SETTLED_UNDER_CHURN
    assert churned["next_recheck_at"] is None


def test_status_shows_the_provisional_captures_and_when_they_are_looked_at_again(
    conn: sqlite3.Connection,
    storage_root: Path,
    video: int,
    meeting: int,
    db_path: Path,
) -> None:
    """Spec 16.3: the state word is on the body's own line and on the watch list."""
    clock = ready_for_recheck(conn, storage_root, video, meeting)
    _reason, _fake = recheck_at(conn, storage_root, clock, CAPTURED + timedelta(hours=1))
    settings = Settings(db_path=db_path, storage_root=storage_root, runtime_root=storage_root)

    waiting = render(collect(settings, clock=lambda: clock.now))
    assert "captions provisional" in waiting
    assert "Captions still being watched (spec 8.7)" in waiting
    assert f"  {PLATFORM_VIDEO_ID}: provisional" in waiting
    # When it was last looked at, and when it is looked at next.
    assert stamp(CAPTURED + timedelta(hours=1)) in waiting
    assert stamp(CAPTURED + timedelta(hours=1) + RECHECK_EVERY) in waiting

    # A revision inside the window, then the backstop at 48 hours: the word a
    # user sees is the one for a source that never stopped changing.
    _reason, _fake = recheck_at(
        conn, storage_root, clock, CAPTURED + timedelta(hours=4), caption=REVISED_SRV3
    )
    end, _fake = recheck_at(conn, storage_root, clock, CAPTURED + FORCED_SETTLE_AFTER)
    assert end is None

    after = render(collect(settings, clock=lambda: clock.now))
    assert "captions settled under churn" in after
    assert "no capture is provisional" in after
    assert f"  {PLATFORM_VIDEO_ID}:" not in after
