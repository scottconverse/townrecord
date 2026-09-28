"""When a capture stops being provisional (spec 8.7).

Spec 8.7 is four sentences of arithmetic over times, and this module is those
four sentences and nothing else. Nothing here reads a database or runs a
command: it takes the moments a caller has already looked up and answers what
to do about them, so a test moves a clock instead of waiting three hours.

The four sentences, in the order they are applied:

* A capture is provisional for 24 hours after the meeting ends, and it is
  rechecked every 3 hours during that time.
* Two unchanged checks, at least 1 hour apart, settle it.
* A capture made more than 24 hours after the meeting is final at once.
* At 48 hours after the first capture, settle it anyway; if the transcript was
  still changing, mark it "settled under churn".

The one thing the spec leaves to be derived is **when the meeting ended**.
``meetings`` carries ``starts_at`` and no end (spec 6.2 keeps the portal's
listing, which has no end), so the end is ``starts_at`` plus the video's
duration. ``records.align`` reads ``starts_at`` as the start of the recording
for the same reason, so the two agree about what a meeting's clock means. When
the duration is unknown the end is unknown, and then the "final at once"
shortcut does not apply: the capture waits for the checks or for the 48 hour
backstop to settle it. That is the conservative reading -- an unknown length
cannot make a capture final early -- and it invents no number.

The 24 hour window governs the shortcut and the cadence, and it does not force
a settle by itself. It cannot: the spec's own last case is a transcript still
changing at 48 hours, which is only possible for a capture that has outlived
its window. What ends a provisional capture is the checks, the shortcut, or
the backstop.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

#: How long after the meeting ends a capture stays provisional (spec 8.7).
PROVISIONAL_WINDOW = timedelta(hours=24)

#: How often a provisional capture is rechecked (spec 8.7).
RECHECK_EVERY = timedelta(hours=3)

#: How far apart two unchanged checks have to be to settle a capture.
SETTLE_APART = timedelta(hours=1)

#: How long after the first capture the backstop settles it anyway.
FORCED_SETTLE_AFTER = timedelta(hours=48)

#: The state word of a capture that may still change (spec 8.7).
PROVISIONAL = "provisional"

#: The state word of a capture that has stopped changing.
SETTLED = "settled"

#: The state word of a capture settled by the 48 hour backstop while it was
#: still changing. Spec 8.7 writes it with a space, and it is a state a user
#: reads rather than an identifier, so it is spelled the way the spec spells it.
SETTLED_UNDER_CHURN = "settled under churn"

#: The three signals of the change test, in the order spec 8.7 gives them. The
#: order is the spec's and is not a preference: a hash that differs is a
#: revision whatever the revision time says, and the revision time is only read
#: when the bytes are the same.
CHANGE_HASH = "hash"
CHANGE_REVISION_AT = "revision_at"
CHANGE_DURATION = "duration_s"

#: The signals in the order they are tested.
CHANGE_ORDER: tuple[str, ...] = (CHANGE_HASH, CHANGE_REVISION_AT, CHANGE_DURATION)


def moment(text: str) -> datetime:
    """Read one timestamp of this system as an aware datetime.

    Every stamp this system writes is UTC with a ``Z``; every stamp it reads
    from elsewhere may carry an offset or none at all. A naive stamp is read as
    UTC, which is what the writer of a naive stamp meant and what
    :func:`townrecord.jobs.stamp` assumes, so the two agree rather than
    differing by the machine's own offset.
    """
    value = text.strip()
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Check:
    """One recheck that has already happened, and what it found."""

    checked_at: datetime
    changed: bool


@dataclass(frozen=True)
class Observation:
    """What the source said at one moment, in the three signals of spec 8.7.

    Any of the three may be None, which means the source did not state it. A
    signal no source states cannot be the one that changed: the test compares
    what is there, and two Nones are not a change.
    """

    sha256: str | None = None
    revision_at: str | None = None
    duration_s: float | None = None

    def differs_from(self, earlier: Observation) -> str | None:
        """Name the first signal that differs, in the spec's order, or None.

        The hash is compared as text, the revision time as the source wrote it
        and not as a parsed moment -- a source that restates the same instant
        in another offset has not revised anything -- and the duration as a
        number.
        """
        if self.sha256 != earlier.sha256:
            return CHANGE_HASH
        if self.revision_at != earlier.revision_at:
            return CHANGE_REVISION_AT
        if self.duration_s != earlier.duration_s:
            return CHANGE_DURATION
        return None


@dataclass(frozen=True)
class Decision:
    """What to do about one capture now: settle it, or recheck it when.

    ``under_churn`` is set only by the backstop, and only when a check found a
    change. ``reason`` is a plain sentence a job reason or a status line can
    carry, because the decision is the thing that knows why.
    """

    settle: bool
    under_churn: bool
    reason: str
    next_check_at: datetime | None


def meeting_end(starts_at: str | None, duration_s: float | None) -> datetime | None:
    """Return when a meeting ended, or None when that cannot be known.

    ``starts_at`` is the portal's listing time and ``duration_s`` the video's
    length; their sum is the end of the recording. None for an unknown start or
    an unknown length: a duration of zero or less describes no meeting, so it
    is treated as unknown rather than as an instant meeting.
    """
    if not starts_at or duration_s is None or duration_s <= 0:
        return None
    return moment(starts_at) + timedelta(seconds=float(duration_s))


def unchanged_pair(checks: Sequence[Check]) -> bool:
    """True when the last two checks are unchanged and at least an hour apart.

    This is the whole of the second rule of spec 8.7. One check is not two, and
    two checks half an hour apart say what one check says, so neither settles
    anything.
    """
    if len(checks) < 2:
        return False
    last, before = checks[-1], checks[-2]
    if last.changed or before.changed:
        return False
    return last.checked_at - before.checked_at >= SETTLE_APART


def churned(checks: Iterable[Check]) -> bool:
    """True when any check found a change. That is what "churn" means here."""
    return any(check.changed for check in checks)


def decide(
    *,
    now: datetime,
    captured_at: datetime,
    meeting_ended_at: datetime | None,
    checks: Sequence[Check],
) -> Decision:
    """Answer whether to settle a capture now, and when to recheck it if not.

    ``checks`` is oldest first and holds only the checks of this video, which
    the caller has already read. ``now`` is the moment the answer is for, so
    the caller's clock is the only clock.
    """
    if meeting_ended_at is not None and captured_at > meeting_ended_at + PROVISIONAL_WINDOW:
        return Decision(
            settle=True,
            under_churn=False,
            reason=(
                "The capture was made more than 24 hours after the meeting ended, so it is "
                "final at once (spec 8.7)."
            ),
            next_check_at=None,
        )

    if unchanged_pair(checks):
        return Decision(
            settle=True,
            under_churn=False,
            reason=(
                "Two checks at least an hour apart found the captions unchanged, so the "
                "capture has settled (spec 8.7)."
            ),
            next_check_at=None,
        )

    backstop = captured_at + FORCED_SETTLE_AFTER
    if now >= backstop:
        # "Still changing" is read off the checks that happened. A capture whose
        # checks all found the same bytes settled by being unchanged and would
        # have been settled above; reaching here with a change on record is the
        # spec's case of a transcript that would not stop moving.
        changed = churned(checks)
        return Decision(
            settle=True,
            under_churn=changed,
            reason=(
                "48 hours have passed since the first capture, so it is settled "
                + ("while it was still changing (spec 8.7)." if changed else "(spec 8.7).")
            ),
            next_check_at=None,
        )

    last = checks[-1].checked_at if checks else captured_at
    due = last + RECHECK_EVERY
    # The backstop is a moment the next check is never put past, so a check
    # taken late does not defer the settle it was meant to trigger.
    return Decision(
        settle=False,
        under_churn=False,
        reason="The capture is still provisional, so it is rechecked (spec 8.7).",
        next_check_at=min(due, backstop),
    )


def state_of(*, is_provisional: bool, settled_under_churn: bool) -> str:
    """Return the state word a user reads for one capture (spec 8.7)."""
    if is_provisional:
        return PROVISIONAL
    return SETTLED_UNDER_CHURN if settled_under_churn else SETTLED


__all__ = [
    "CHANGE_DURATION",
    "CHANGE_HASH",
    "CHANGE_ORDER",
    "CHANGE_REVISION_AT",
    "FORCED_SETTLE_AFTER",
    "PROVISIONAL",
    "PROVISIONAL_WINDOW",
    "RECHECK_EVERY",
    "SETTLED",
    "SETTLED_UNDER_CHURN",
    "SETTLE_APART",
    "Check",
    "Decision",
    "Observation",
    "churned",
    "decide",
    "meeting_end",
    "moment",
    "state_of",
    "unchanged_pair",
]
