"""Failover down a ladder, and what each call is recorded as (spec 11.5).

The rules are TownReporter's, and they are short enough to state as data:

* move one rung down for an expired sign-in, a time-out or empty output, a
  quota refusal, an unavailable provider, or an unreadable reply;
* do not move for a content refusal. That is final;
* a user-picked local model fails closed and is never sent to a cloud
  provider without the user's choice. The model is what is asked, so a local
  program the user picked, holding a model that runs in that program's own
  cloud, is refused before the first call: a cloud model behind a local
  program is not a local model (spec 2, 11.5);
* record, for each call, the model that was requested, the model that ran, and
  why it was chosen.

A reason that is not on either list does not move the ladder. An unrecognized
failure is not one of the six the spec lists, and guessing that it is would
send work to a second provider on the strength of a message nobody classified.

Nothing here calls a model. :func:`run_task` takes the call as an argument, so
the caller owns the HTTP client or the process runner and this module stays
offline, which is what lets the tests drive every rule above with fakes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .ladder import Ladder, Resolution, Rung
from .providers import KIND_LOCAL, ModelHome, Provider, ProviderRegistry

#: The failures that move the ladder one rung down (spec 11.5).
REASON_EXPIRED_SIGN_IN = "expired sign-in"
REASON_TIMEOUT = "time-out"
REASON_EMPTY_OUTPUT = "empty output"
REASON_QUOTA = "quota refusal"
REASON_UNAVAILABLE = "provider unavailable"
REASON_UNREADABLE_REPLY = "unreadable reply"

#: The one failure that is final (spec 11.5). A model that refused the content
#: refused it; asking a second one is asking the user to be refused twice.
REASON_CONTENT_REFUSAL = "content refusal"

MOVE_REASONS: frozenset[str] = frozenset(
    {
        REASON_EXPIRED_SIGN_IN,
        REASON_TIMEOUT,
        REASON_EMPTY_OUTPUT,
        REASON_QUOTA,
        REASON_UNAVAILABLE,
        REASON_UNREADABLE_REPLY,
    }
)

FINAL_REASONS: frozenset[str] = frozenset({REASON_CONTENT_REFUSAL})

#: Every reason the failover rules name, in the order spec 11.5 lists them.
REASONS: tuple[str, ...] = (
    REASON_EXPIRED_SIGN_IN,
    REASON_TIMEOUT,
    REASON_EMPTY_OUTPUT,
    REASON_QUOTA,
    REASON_UNAVAILABLE,
    REASON_UNREADABLE_REPLY,
    REASON_CONTENT_REFUSAL,
)


def moves_on(reason: str) -> bool:
    """True when this failure moves the ladder one rung down (spec 11.5)."""
    if reason in FINAL_REASONS:
        return False
    return reason in MOVE_REASONS


@dataclass(frozen=True)
class Attempt:
    """What one call to one model did."""

    ok: bool
    output: str = ""
    #: One of :data:`REASONS`, or empty when the call worked.
    reason: str = ""
    #: The failure in the provider's own words, already redacted by the caller.
    detail: str = ""


@dataclass(frozen=True)
class CallRecord:
    """The three things spec 11.5 says to record for each call."""

    task: str
    #: The model the task's setting asked for.
    requested: str
    #: The model that ran.
    ran: str
    #: Why that one was chosen.
    chosen_because: str
    #: Why the call ended as it did, or empty when it worked.
    reason: str = ""
    detail: str = ""

    def sentence(self) -> str:
        """One plain sentence, with no secret in it (spec 11.2)."""
        if self.reason:
            return (
                f"{self.task} was requested from {self.requested} and ran on {self.ran} "
                f"({self.chosen_because}). It failed with a {self.reason}."
            )
        return (
            f"{self.task} was requested from {self.requested} and ran on {self.ran} "
            f"({self.chosen_because})."
        )


@dataclass(frozen=True)
class TaskRun:
    """How a whole task ended: what ran, what it wrote, and every call made."""

    task: str
    ok: bool
    output: str = ""
    records: tuple[CallRecord, ...] = ()
    sentence: str = ""
    resolution: Resolution | None = None
    #: Rungs skipped before the first call, in the resolution's words.
    skipped: tuple[str, ...] = ()
    #: True when the ladder stopped because the user picked a local model.
    failed_closed: bool = False


def run_task(
    *,
    ladder: Ladder,
    registry: ProviderRegistry,
    call: Callable[[Provider, Rung], Attempt],
    reachable: Callable[[Provider], bool] | None = None,
) -> TaskRun:
    """Run one task down its ladder, following the failover rules.

    ``call`` is handed the provider and the rung and returns an
    :class:`Attempt`. It is called at most once per rung: a rung that failed
    with a move reason is never retried, because the reason it failed is not a
    reason that goes away.
    """
    resolution = ladder.resolve(registry, reachable=reachable)
    if not resolution.ok:
        return TaskRun(
            task=ladder.task,
            ok=False,
            sentence=resolution.sentence(),
            resolution=resolution,
            skipped=resolution.skipped,
        )

    requested = _label(resolution.provider, resolution.rung)
    rung_model = resolution.rung.model if resolution.rung is not None else ""
    home = resolution.provider.model_home(rung_model)
    if not ladder.is_automatic and resolution.provider.kind == KIND_LOCAL and not home.is_local:
        # The user picked a local program, and the model it would run does not
        # run on this machine: a cloud model behind a local program (spec 2,
        # 11.5). Nothing is called, because a call is the thing that would send
        # the minutes away, and failing closed after one is already too late.
        # A provider the user picked from a vendor is their own choice of the
        # cloud, and an automatic ladder is a list they wrote, so neither of
        # those is refused here.
        return TaskRun(
            task=ladder.task,
            ok=False,
            sentence=_refused_sentence(ladder, resolution.provider.name, home),
            resolution=resolution,
            skipped=resolution.skipped,
            failed_closed=True,
        )
    records: list[CallRecord] = []
    index = resolution.index
    while index is not None:
        rung = ladder.rungs[index]
        provider = registry.find(name=rung.provider, kind=rung.kind)
        if provider is None:
            break
        chosen_because = _chosen_because(index, resolution, records)
        attempt = call(provider, rung)
        records.append(
            CallRecord(
                task=ladder.task,
                requested=requested,
                ran=_label(provider, rung),
                chosen_because=chosen_because,
                reason=attempt.reason,
                detail=attempt.detail,
            )
        )
        if attempt.ok:
            return TaskRun(
                task=ladder.task,
                ok=True,
                output=attempt.output,
                records=tuple(records),
                sentence=_ran_sentence(records),
                resolution=resolution,
                skipped=resolution.skipped,
            )
        if not moves_on(attempt.reason):
            return TaskRun(
                task=ladder.task,
                ok=False,
                records=tuple(records),
                sentence=_final_sentence(records, attempt),
                resolution=resolution,
                skipped=resolution.skipped,
            )
        if ladder.fails_closed(registry):
            return TaskRun(
                task=ladder.task,
                ok=False,
                records=tuple(records),
                sentence=(
                    f"{provider.describe()} is the local model this task was set to, so the "
                    f"{ladder.task} work is not sent to another provider. It failed with a "
                    f"{attempt.reason}."
                ),
                resolution=resolution,
                skipped=resolution.skipped,
                failed_closed=True,
            )
        index = ladder.next_after(index)

    return TaskRun(
        task=ladder.task,
        ok=False,
        records=tuple(records),
        sentence=(
            f"Every rung of the {ladder.task} ladder failed: "
            f"{'; '.join(record.sentence() for record in records)}."
        ),
        resolution=resolution,
        skipped=resolution.skipped,
    )


def _refused_sentence(ladder: Ladder, provider_name: str, home: ModelHome) -> str:
    """One plain sentence about work that was not sent, and what to do instead."""
    return (
        f"Nothing was sent to the model. {provider_name} is set to {home.model}, but "
        f"{home.sentence()} This {ladder.task} work stays on this machine: pick a model that "
        f"runs here, or set {ladder.task} to Automatic."
    )


def _label(provider: Provider | None, rung: Rung | None) -> str:
    """How one rung's model is written in a record."""
    if provider is None:
        return rung.describe() if rung is not None else "nothing"
    return f"{provider.name}/{rung.model}" if rung is not None and rung.model else provider.name


def _chosen_because(index: int, resolution: Resolution, records: list[CallRecord]) -> str:
    """Why this rung is the one running."""
    if index == resolution.index:
        if resolution.skipped:
            return f"it is the first rung that is reachable, after skipping {resolution.skipped[0]}"
        return "it is the first rung of the ladder"
    previous = records[-1]
    return f"the rung above it failed with a {previous.reason}"


def _ran_sentence(records: list[CallRecord]) -> str:
    """One plain sentence about a task that ran."""
    last = records[-1]
    if len(records) == 1:
        return f"{last.task} ran on {last.ran}."
    return f"{last.task} ran on {last.ran}, after {len(records) - 1} failed rung(s)."


def _final_sentence(records: list[CallRecord], attempt: Attempt) -> str:
    """One plain sentence about a task that stopped, and why it did not move."""
    last = records[-1]
    detail = f" The provider said: {attempt.detail}" if attempt.detail else ""
    return (
        f"{last.task} did not run: {last.ran} answered with a {attempt.reason}, which is final, "
        f"so the ladder did not move down.{detail}"
    )
