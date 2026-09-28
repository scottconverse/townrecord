"""Tasks and the ladder that answers each one (spec 11.4).

Every task has one setting. Either the user picked a provider and a model, or
the setting is "Automatic", which is an ordered list of providers the user can
edit. The first rung that is reachable runs the task.

A rung names a provider by name, or names a provider *kind* and takes the first
provider of that kind. The kind form is what the built-in ladders use, and it
is the honest default: this program does not know what the user has called
their Ollama, and a default ladder that invented a name would point at nothing.
The user edits a ladder into names the moment they care which one runs.

The tasks are the ones of spec 11.4. Transcribe is deliberately absent: it is
TextFlowKit on this machine (:mod:`townrecord.stt`), which is not a provider in
this registry, and a ladder for it would be a ladder with one rung that is not
a model at all.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .providers import (
    KIND_CLAUDE_CLI,
    KIND_CODEX_CLI,
    KIND_LOCAL,
    KINDS,
    Provider,
    ProviderRegistry,
)

#: The tasks of spec 11.4, spelled as that section's table spells them.
TASK_CLASSIFY = "classify"
TASK_DISCOVER = "discover"
TASK_ALIGN = "align"
TASK_SUMMARIZE = "summarize"
TASK_ANSWER = "answer"
TASK_FACT_CHECK = "fact-check"
TASK_OCR = "ocr"
TASK_EMBED = "embed"

TASKS: tuple[str, ...] = (
    TASK_CLASSIFY,
    TASK_DISCOVER,
    TASK_ALIGN,
    TASK_SUMMARIZE,
    TASK_ANSWER,
    TASK_FACT_CHECK,
    TASK_OCR,
    TASK_EMBED,
)

#: "Automatic" and "the one the user picked" (spec 11.4).
MODE_AUTOMATIC = "automatic"
MODE_PROVIDER = "provider"
MODES: tuple[str, ...] = (MODE_AUTOMATIC, MODE_PROVIDER)

#: What the user sees for the setting that runs a ladder.
AUTOMATIC_LABEL = "Automatic"


@dataclass(frozen=True)
class Rung:
    """One step of a ladder: a provider by name, or the first of a kind."""

    provider: str = ""
    kind: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        if not self.provider and not self.kind:
            raise ValueError("A rung names a provider or a provider kind.")
        if self.kind and self.kind not in KINDS:
            raise ValueError(
                f"{self.kind!r} is not a provider kind. The kinds are: {', '.join(KINDS)}."
            )

    def as_json(self) -> dict[str, str]:
        """The rung as it is stored in ``ai_tasks.ladder``."""
        return {"provider": self.provider, "kind": self.kind, "model": self.model}

    @classmethod
    def from_json(cls, data: Any) -> Rung:
        """Read one stored rung. A rung that cannot be read is refused."""
        if not isinstance(data, Mapping):
            raise ValueError(f"A rung is a JSON object, not {data!r}.")
        return cls(
            provider=str(data.get("provider", "") or ""),
            kind=str(data.get("kind", "") or ""),
            model=str(data.get("model", "") or ""),
        )

    def describe(self) -> str:
        """How this rung is written in a sentence or a record."""
        who = self.provider or f"the first {self.kind} provider"
        return f"{who} running {self.model}" if self.model else who


@dataclass(frozen=True)
class Ladder:
    """One task's model setting: a picked provider, or an ordered ladder."""

    task: str
    rungs: tuple[Rung, ...]
    mode: str = MODE_AUTOMATIC

    def __post_init__(self) -> None:
        if self.task not in TASKS:
            raise ValueError(f"{self.task!r} is not a task. The tasks are: {', '.join(TASKS)}.")
        if self.mode not in MODES:
            raise ValueError(f"{self.mode!r} is not a mode. The modes are: {', '.join(MODES)}.")
        if not self.rungs:
            raise ValueError(f"The {self.task} ladder has no rungs, so it can never run.")

    @classmethod
    def automatic(cls, task: str, rungs: tuple[Rung, ...] | list[Rung]) -> Ladder:
        """The setting that runs the first reachable rung of ``rungs``."""
        return cls(task=task, rungs=tuple(rungs), mode=MODE_AUTOMATIC)

    @classmethod
    def picked(cls, task: str, provider: str, model: str = "") -> Ladder:
        """The setting that runs one provider, and fails closed if it is local."""
        return cls(task=task, rungs=(Rung(provider=provider, model=model),), mode=MODE_PROVIDER)

    @classmethod
    def from_json(cls, task: str, data: Any) -> Ladder:
        """Read a ladder out of the ``ai_tasks`` columns of migration 0016."""
        if not isinstance(data, Mapping):
            raise ValueError(f"A stored ladder is a JSON object, not {data!r}.")
        mode = str(data.get("mode", "") or MODE_AUTOMATIC)
        if mode == MODE_PROVIDER:
            provider = str(data.get("provider", "") or "")
            if not provider:
                raise ValueError("A picked ladder names the provider the user picked.")
            return cls.picked(task, provider, str(data.get("model", "") or ""))
        raw = data.get("ladder", [])
        if not isinstance(raw, (list, tuple)) or not raw:
            return cls.automatic(task, default_rungs(task))
        return cls.automatic(task, [Rung.from_json(rung) for rung in raw])

    @property
    def is_automatic(self) -> bool:
        """True when this task runs a ladder rather than one picked provider."""
        return self.mode == MODE_AUTOMATIC

    def first(self) -> Rung:
        """The top rung."""
        return self.rungs[0]

    def next_after(self, index: int) -> int | None:
        """The rung below ``index``, or None when there is none (spec 11.5)."""
        following = index + 1
        return following if index >= 0 and following < len(self.rungs) else None

    def fails_closed(self, registry: ProviderRegistry) -> bool:
        """True when this work must not leave the local provider it was given.

        Spec 11.5: if the user picked a specific local model, fail closed and
        never send that work to a cloud provider without the user's choice.
        Only a picked setting can fail closed: an automatic ladder is a list
        the user wrote, and moving down it is the choice they already made.

        The model is asked, not only the provider. A local program can serve a
        model from its own cloud, so "the user picked a local model" is only
        true when the model itself runs on this machine. A picked model that
        runs in the cloud is not local work at all, and
        :func:`townrecord.ai.failover.run_task` refuses it before the first
        call rather than failing closed after one.
        """
        if self.is_automatic:
            return False
        provider = registry.find(name=self.first().provider, kind=self.first().kind)
        if provider is None:
            # A picked provider this program cannot find is not evidence that
            # it is in the cloud. The user named one provider; a name that no
            # longer resolves keeps the work here until the user says
            # otherwise, which is the direction that cannot leak the minutes of
            # a meeting to a vendor by accident. A rung that names a command
            # kind is the one case that is not local, and it is not found here
            # either: the program is on PATH, not in the registry.
            return self.first().kind in ("", KIND_LOCAL)
        return provider.is_local(self.first().model)

    def resolve(
        self, registry: ProviderRegistry, *, reachable: Callable[[Provider], bool] | None = None
    ) -> Resolution:
        """Return the first rung that can run, and what was skipped to reach it.

        ``reachable`` is how a caller says a provider cannot be asked right now
        (its program is missing, its address does not answer, its key is not
        set). It is a plain callable so this module stays offline: the caller
        that owns an HTTP client or a process runner is the one that knows.
        """
        skipped: list[str] = []
        for index, rung in enumerate(self.rungs):
            provider = registry.find(name=rung.provider, kind=rung.kind)
            if provider is None:
                skipped.append(f"{rung.describe()} is not configured")
                continue
            if reachable is not None and not reachable(provider):
                skipped.append(f"{provider.describe()} is not reachable")
                continue
            return Resolution(
                ladder=self,
                index=index,
                rung=rung,
                provider=provider,
                skipped=tuple(skipped),
            )
        return Resolution(ladder=self, index=None, rung=None, provider=None, skipped=tuple(skipped))


@dataclass(frozen=True)
class Resolution:
    """The rung that runs a task, and the rungs that were passed over."""

    ladder: Ladder
    index: int | None
    rung: Rung | None
    provider: Provider | None
    skipped: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when some rung of the ladder can run."""
        return self.provider is not None

    def sentence(self) -> str:
        """One plain sentence about how this task's model was chosen."""
        if self.provider is None:
            tried = "; ".join(self.skipped) or "no rungs are configured"
            return f"No model can run {self.ladder.task} right now: {tried}."
        ran = f"{self.provider.describe()}"
        if self.rung is not None and self.rung.model:
            ran = f"{ran} running {self.rung.model}"
        if not self.skipped:
            return f"{self.ladder.task} runs on {ran}."
        return f"{self.ladder.task} runs on {ran}, after skipping {', '.join(self.skipped)}."


def default_rungs(task: str) -> tuple[Rung, ...]:
    """Return the built-in ladder for a task (spec 11.4's task table).

    Each entry follows the "typical model" column: a small local model
    classifies and aligns, a local or cloud model summarizes and discovers,
    a cloud or subscription model answers and fact-checks, and OCR and
    embedding are local work that never leaves the machine.
    """
    if task not in DEFAULT_LADDERS:
        raise ValueError(f"{task!r} is not a task. The tasks are: {', '.join(TASKS)}.")
    return DEFAULT_LADDERS[task]


#: The built-in automatic ladders, in the order spec 11.4's table gives each
#: task's typical model. They name kinds rather than providers because this
#: program does not know what the user called their Ollama: the first provider
#: of that kind is the one that answers, and the user replaces a rung with a
#: name as soon as they care which one runs.
DEFAULT_LADDERS: Mapping[str, tuple[Rung, ...]] = {
    TASK_CLASSIFY: (Rung(kind=KIND_LOCAL),),
    TASK_DISCOVER: (Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)),
    TASK_ALIGN: (Rung(kind=KIND_LOCAL),),
    TASK_SUMMARIZE: (Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)),
    TASK_ANSWER: (Rung(kind=KIND_CLAUDE_CLI), Rung(kind=KIND_CODEX_CLI), Rung(kind=KIND_LOCAL)),
    TASK_FACT_CHECK: (Rung(kind=KIND_CLAUDE_CLI), Rung(kind=KIND_CODEX_CLI)),
    TASK_OCR: (Rung(kind=KIND_LOCAL),),
    TASK_EMBED: (Rung(kind=KIND_LOCAL),),
}


def default_ladder(task: str) -> Ladder:
    """Return the built-in automatic ladder for a task."""
    return Ladder.automatic(task, default_rungs(task))
