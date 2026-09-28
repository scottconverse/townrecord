"""Time budgets per provider kind and per task (spec 11.6).

A budget is a stopping point, not a hope: a command-line program that has run
for seven minutes is doing something nobody asked for, and a call that has run
for ten minutes on a local model is competing with the user's own work.

TownReporter's defaults are the ones below, and they are a property of the way
a provider answers rather than of a task: a local model is slow and free, an
API is fast and paid, and a subscription program is somewhere between. The user
changes them per task, which is why the overrides arrive keyed by provider kind
(the ``ai_tasks.budgets`` column of migration 0016).

The kind is not quite the whole answer. A local program can serve a model from
its own cloud, and forty minutes per job is right for a model on this machine
and wrong for one that is an API call (spec 11.5, 11.6). So :func:`for_task`
takes where the model runs and sizes the budget by that.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from .providers import (
    KIND_ANTHROPIC,
    KIND_CLAUDE_CLI,
    KIND_CODEX_CLI,
    KIND_LOCAL,
    KIND_OPENAI,
    KIND_OPENAI_COMPATIBLE,
    KINDS,
    ModelHome,
)


@dataclass(frozen=True)
class Budget:
    """How long one job and one call of a provider kind may take."""

    #: Seconds the whole job may take, across every call it makes.
    job_s: float
    #: Seconds one call may take.
    call_s: float

    def __post_init__(self) -> None:
        if self.job_s <= 0 or self.call_s <= 0:
            raise ValueError(f"A budget is a positive number of seconds, not {self!r}.")


#: The defaults of spec 11.6, by provider kind. The command-line figure is
#: 420 seconds per job and 150 per call, an API's is 38 and 20, and a local
#: model gets 40 minutes per job and 10 minutes per call.
DEFAULT_BUDGETS: Mapping[str, Budget] = {
    KIND_CLAUDE_CLI: Budget(job_s=420.0, call_s=150.0),
    KIND_CODEX_CLI: Budget(job_s=420.0, call_s=150.0),
    KIND_ANTHROPIC: Budget(job_s=38.0, call_s=20.0),
    KIND_OPENAI: Budget(job_s=38.0, call_s=20.0),
    KIND_OPENAI_COMPATIBLE: Budget(job_s=38.0, call_s=20.0),
    KIND_LOCAL: Budget(job_s=2400.0, call_s=600.0),
}

#: The budget a model in the cloud gets, even when the row that reaches it is a
#: local program's row (spec 11.6). Forty minutes per job is right for a model
#: on this machine and wrong for one Ollama serves from its own cloud, which is
#: an API call however the user's row is spelled, so it gets the API budget.
BUDGET_CLOUD_MODEL: Budget = DEFAULT_BUDGETS[KIND_OPENAI]

#: The seconds one call of a local model is expected to take when a caller only
#: has a total to work from. Spec 11.6 measured a four-hour council meeting
#: draft at about 20 minutes across nine local-model calls.
MEASURED_CALL_S = 20.0 * 60.0 / 9.0


def for_kind(kind: str, *, overrides: Mapping[str, Mapping[str, float]] | None = None) -> Budget:
    """Return the budget for a provider kind, with this task's overrides.

    An override is written under the kind's own name, so a task can give its
    local calls a longer per-call budget without touching what an API call
    gets. A kind with no default and no override is refused: guessing what a
    new kind of provider may take is how a job hangs.
    """
    return _with_overrides(_base_budget(kind), kind, overrides)


def for_task(
    task: str,
    kind: str,
    *,
    home: ModelHome | None = None,
    overrides: Mapping[str, Mapping[str, float]] | None = None,
) -> Budget:
    """Return the budget for one task on one provider kind (spec 11.6).

    ``home`` is where the model this task is about to run on actually runs. A
    local program's row gets the local budget only when the model is local too:
    the same row can serve a model from a cloud, and that one is an API call
    (:data:`BUDGET_CLOUD_MODEL`). A caller that has not worked out where the
    model runs passes nothing, and the kind's own budget is the answer.
    """
    del task  # The task only says which overrides were read; the kind sizes them.
    base = _base_budget(kind)
    if home is not None and not home.is_local:
        base = BUDGET_CLOUD_MODEL
    return _with_overrides(base, kind, overrides)


def _base_budget(kind: str) -> Budget:
    """The built-in budget of one kind, or a refusal naming the kinds that have one."""
    base = DEFAULT_BUDGETS.get(kind)
    if base is None:
        raise ValueError(f"{kind!r} has no time budget. The kinds are: {', '.join(KINDS)}.")
    return base


def _with_overrides(
    base: Budget, kind: str, overrides: Mapping[str, Mapping[str, float]] | None
) -> Budget:
    """``base`` with this task's overrides for ``kind``, which are its own kind."""
    chosen = (overrides or {}).get(kind)
    if not chosen:
        return base
    return Budget(
        job_s=float(chosen.get("job_s", base.job_s)),
        call_s=float(chosen.get("call_s", base.call_s)),
    )


def parse_overrides(text: str, *, what: str = "budgets") -> tuple[dict[str, dict[str, float]], str]:
    """Read a task's stored budget overrides, and say why when they cannot be.

    The answer is the overrides and a plain sentence, empty when there was
    nothing wrong. An unreadable setting is the built-in default and a reason,
    never a crash (spec 16.3), the same rule the per-source settings follow.
    """
    raw = str(text or "").strip()
    if not raw:
        return {}, ""
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {}, f"{what} for this task are not JSON ({exc}), so the built-in ones are used"
    if not isinstance(loaded, Mapping):
        return {}, f"{what} for this task are not a JSON object, so the built-in ones are used"
    overrides: dict[str, dict[str, float]] = {}
    for kind, values in loaded.items():
        if not isinstance(values, Mapping):
            continue
        overrides[str(kind)] = {
            str(name): float(value)
            for name, value in values.items()
            if name in ("job_s", "call_s") and _is_number(value)
        }
    return overrides, ""


def _is_number(value: object) -> bool:
    try:
        float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return True
