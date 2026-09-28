"""The provider registry (spec 11.1, 11.2).

One place defines every model the application can use, and that place is
configuration rather than code: a provider is a row of ``ai_providers``
(migration 0016), and the kinds here say how a row of that kind connects.
Adding a local model or a new gateway is a setting the user changes, and never
a release.

Keys live in the row and nowhere else. Two habits keep them out of the places
they leak from, and they live here because they are properties of a provider:

* :meth:`Provider.__repr__` prints ``[redacted]`` in place of the key, because
  a repr ends up in a log or a traceback;
* :func:`redact` takes every known secret, and anything shaped like one, out of
  text before a caller prints it (spec 11.2).

Neither is a substitute for the other, and the test suite checks both.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

#: The provider kinds of spec 11.1, spelled as the ``ai_providers.kind``
#: column's CHECK spells them.
KIND_LOCAL = "local"
KIND_OPENAI_COMPATIBLE = "openai_compatible"
KIND_ANTHROPIC = "anthropic"
KIND_OPENAI = "openai"
KIND_CLAUDE_CLI = "claude_cli"
KIND_CODEX_CLI = "codex_cli"

KINDS: tuple[str, ...] = (
    KIND_LOCAL,
    KIND_OPENAI_COMPATIBLE,
    KIND_ANTHROPIC,
    KIND_OPENAI,
    KIND_CLAUDE_CLI,
    KIND_CODEX_CLI,
)

#: The kinds that are answered by a request over HTTP.
HTTP_KINDS: tuple[str, ...] = (
    KIND_LOCAL,
    KIND_OPENAI_COMPATIBLE,
    KIND_ANTHROPIC,
    KIND_OPENAI,
)

#: The kinds that run as a program on this machine (spec 11.2, 11.3).
COMMAND_KINDS: tuple[str, ...] = (KIND_CLAUDE_CLI, KIND_CODEX_CLI)

#: The kinds whose vendor API needs an API key (spec 11.1). A local program and
#: an OpenAI-compatible gateway are deliberately absent: a local program has no
#: account at all, and a gateway is the user's own address, which may be open.
KEY_KINDS: tuple[str, ...] = (KIND_ANTHROPIC, KIND_OPENAI)

#: The program each command kind runs (spec 11.3).
PROGRAM_NAMES: Mapping[str, str] = {
    KIND_CLAUDE_CLI: "claude",
    KIND_CODEX_CLI: "codex",
}

#: The address a kind answers at when the user has not named one. The local
#: address is Ollama's own default port; :mod:`townrecord.ai.discovery` probes
#: that port and the other two local programs' ports on the loopback address.
DEFAULT_BASE_URLS: Mapping[str, str] = {
    KIND_LOCAL: "http://127.0.0.1:11434",
    KIND_OPENAI_COMPATIBLE: "",
    KIND_ANTHROPIC: "https://api.anthropic.com",
    KIND_OPENAI: "https://api.openai.com/v1",
    KIND_CLAUDE_CLI: "",
    KIND_CODEX_CLI: "",
}

#: What stands in for a secret in text that is printed (spec 11.2).
REDACTED = "[redacted]"

#: The shortest value worth replacing by name. Below this a "secret" is short
#: enough to appear inside ordinary words, and a redactor that mangles every
#: sentence it touches is one a caller turns off.
MIN_SECRET_LENGTH = 4

#: The shapes a secret has when it was never handed to us as one: an Anthropic
#: or OpenAI key, a bearer token, or a value written after a key's own name.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|access[_-]?token|token|secret)\b\s*[:=]\s*\"?[A-Za-z0-9._\-]{8,}"
    ),
)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Return ``text`` with every secret taken out of it (spec 11.2).

    The values the caller names are replaced first, because they are the ones
    that are certainly secret. The patterns then catch a key nobody named:
    a token read out of a reply, or a whole environment that was printed by a
    program we ran.
    """
    answer = str(text)
    for secret in secrets:
        value = str(secret).strip()
        if len(value) >= MIN_SECRET_LENGTH:
            answer = answer.replace(value, REDACTED)
    for pattern in _SECRET_PATTERNS:
        answer = pattern.sub(REDACTED, answer)
    return answer


@dataclass(frozen=True)
class Provider:
    """One model the application can use, as the user configured it.

    It is a row of ``ai_providers`` with the columns of that table, plus the
    row's id once it has one. Nothing here reaches the network: the fields say
    where a request would go.
    """

    name: str
    kind: str
    base_url: str = ""
    api_key: str = ""
    models: tuple[str, ...] = ()
    settings: Mapping[str, str] = field(default_factory=dict)
    id: int | None = None

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise ValueError("A provider needs a name.")
        if self.kind not in KINDS:
            raise ValueError(
                f"{self.kind!r} is not a provider kind. The kinds are: {', '.join(KINDS)}."
            )

    def __repr__(self) -> str:
        """Show the provider without its key: a repr is what a log holds."""
        key = mask(self.api_key)
        return (
            f"Provider(name={self.name!r}, kind={self.kind!r}, "
            f"base_url={self.endpoint!r}, api_key={key!r}, models={self.models!r}, id={self.id!r})"
        )

    @property
    def endpoint(self) -> str:
        """The address this provider answers at, or the kind's own default."""
        return self.base_url.strip() or DEFAULT_BASE_URLS.get(self.kind, "")

    @property
    def requires_key(self) -> bool:
        """True when this kind's vendor API needs a key (spec 11.1)."""
        return self.kind in KEY_KINDS

    @property
    def is_local(self) -> bool:
        """True when the model runs on this machine (spec 11.5, 11.8)."""
        return self.kind == KIND_LOCAL

    @property
    def is_command(self) -> bool:
        """True when this provider is a program on this machine (spec 11.3)."""
        return self.kind in COMMAND_KINDS

    @property
    def program(self) -> str:
        """The program this provider runs, or an empty string for the rest."""
        return PROGRAM_NAMES.get(self.kind, "")

    def secrets(self) -> tuple[str, ...]:
        """Every secret this provider holds, for :func:`redact`."""
        return (self.api_key,) if self.api_key else ()

    def redact(self, text: str) -> str:
        """Return ``text`` with this provider's secret taken out of it."""
        return redact(text, self.secrets())

    def describe(self) -> str:
        """One plain sentence about this provider, with no key in it."""
        if self.is_local:
            return f"{self.name}, a local model at {self.endpoint}"
        if self.is_command:
            return f"{self.name}, the {self.program} program"
        return f"{self.name}, {self.kind} at {self.endpoint}"


def mask(value: str) -> str:
    """Return a value fit to print in place of a secret. Empty stays empty."""
    return REDACTED if str(value).strip() else ""


class ProviderRegistry:
    """Every provider the user has configured, found by name or by kind.

    It is the one place a caller looks a model up in (spec 11.1). It is not
    frozen: the user adds and removes providers while the service runs, and a
    ladder resolves against the registry as it stands when the task starts.
    """

    def __init__(self, providers: Iterable[Provider] = ()) -> None:
        self._by_name: dict[str, Provider] = {}
        for provider in providers:
            self.add(provider)

    def add(self, provider: Provider) -> Provider:
        """Put a provider in the registry, replacing one of the same name."""
        self._by_name[provider.name] = provider
        return provider

    def get(self, name: str) -> Provider | None:
        """Return the provider with this name, or None."""
        return self._by_name.get(str(name))

    def of_kind(self, kind: str) -> tuple[Provider, ...]:
        """Return every provider of one kind, in the order they were added."""
        return tuple(provider for provider in self._by_name.values() if provider.kind == kind)

    def first_of_kind(self, kind: str) -> Provider | None:
        """Return the first provider of one kind, or None."""
        found = self.of_kind(kind)
        return found[0] if found else None

    def find(self, *, name: str = "", kind: str = "") -> Provider | None:
        """Return the provider a rung names: by name first, then by kind."""
        if name:
            return self.get(name)
        return self.first_of_kind(kind) if kind else None

    def names(self) -> tuple[str, ...]:
        """Every provider name, in the order they were added."""
        return tuple(self._by_name)

    def __len__(self) -> int:
        return len(self._by_name)

    def __iter__(self):
        return iter(self._by_name.values())

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __repr__(self) -> str:
        return f"ProviderRegistry({', '.join(self.names())})"
