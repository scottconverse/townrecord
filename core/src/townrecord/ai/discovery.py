"""Finding the local model programs and listing what they hold (spec 11.1, 11.8).

Ollama, LM Studio and llama.cpp run on this machine and answer on the loopback
address at their own default ports. This module asks each of them, on 127.0.0.1
and on [::1], for the list of models it has, and this module is the only place
in the application that talks to them before the user has chosen anything.

Two rules are load-bearing:

* it never loads or unloads a model (spec 11.8). TownReporter's desk "never
  loads or unloads a model", and the way to keep that true is to ask only for a
  list: :data:`READ_ONLY_PATHS` is the whole vocabulary, and
  :func:`_read_only_url` refuses anything else before a request is made;
* it says where each model runs. A local program is not a local model: Ollama
  serves some of its models from its own cloud, and a user who picked one of
  those while the task is set to a local model would be sending a meeting to a
  vendor (spec 2, 11.5). Each entry of the reply is read through
  :func:`townrecord.ai.providers.model_home`, and :meth:`Found.lines` writes out
  what it said;
* a discovery that finds nothing says what it tried. An empty list is not an
  answer a user can act on, so the result carries every address it asked and
  why each one stayed quiet, and :meth:`DiscoveryResult.sentence` writes it out.

Nothing here needs the network beyond the loopback address, and a test drives
it with an ``httpx.MockTransport`` (rule 7).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from .providers import KIND_LOCAL, ModelHome, Provider, model_home

#: The two spellings of the loopback address every endpoint is tried on.
LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "[::1]")

#: Ollama answers its own shape; the other two answer the OpenAI one.
FORM_OLLAMA = "ollama"
FORM_OPENAI = "openai"

#: The paths a discovery may ask for, and nothing else (spec 11.8). A load or
#: an unload is never in this set, so it can never be asked for.
READ_ONLY_PATHS: frozenset[str] = frozenset({"/api/tags", "/v1/models"})


@dataclass(frozen=True)
class LocalEndpoint:
    """One local program on its own default port."""

    program: str
    port: int
    list_path: str
    form: str

    def url(self, host: str) -> str:
        """The list address for this endpoint on one loopback spelling."""
        return f"http://{host}:{self.port}{self.list_path}"

    def base_url(self, host: str) -> str:
        """The address a provider row of this endpoint would carry."""
        return f"http://{host}:{self.port}"


#: The three programs of spec 11.1 at their own default ports: Ollama 11434,
#: LM Studio 1234, llama.cpp's server 8080. Each one's list path is its own.
LOCAL_ENDPOINTS: tuple[LocalEndpoint, ...] = (
    LocalEndpoint("ollama", 11434, "/api/tags", FORM_OLLAMA),
    LocalEndpoint("lmstudio", 1234, "/v1/models", FORM_OPENAI),
    LocalEndpoint("llamacpp", 8080, "/v1/models", FORM_OPENAI),
)

#: How long one list request may take. A local server that is not running
#: refuses at once; one that is running answers at once. A wait longer than
#: this is a program that is not the one we are looking for.
DEFAULT_PROBE_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class Found:
    """One local program that answered, and the models it listed.

    Each model is reported with where it runs and why (spec 11.5): a local
    program can serve a model from its own cloud, and a listing that showed
    only names would leave a user picking one of those believing it runs on
    their own machine. ``homes`` is read from the entry each model was listed
    in, which is where Ollama's own answer lives; a caller that builds a
    ``Found`` from names alone gets the name-only reading of the same rule.
    """

    program: str
    base_url: str
    models: tuple[str, ...]
    form: str = FORM_OLLAMA
    #: Where each model of ``models`` runs, in the same order.
    homes: tuple[ModelHome, ...] = ()

    def __post_init__(self) -> None:
        if not self.homes and self.models:
            object.__setattr__(
                self,
                "homes",
                tuple(model_home(name, program=self.program) for name in self.models),
            )

    def as_provider(self, name: str = "") -> Provider:
        """The provider row this program would be saved as."""
        return Provider(
            name=name or self.program,
            kind=KIND_LOCAL,
            base_url=self.base_url,
            models=self.models,
        )

    @property
    def here(self) -> tuple[ModelHome, ...]:
        """The models of this program that run on this machine."""
        return tuple(home for home in self.homes if home.is_local)

    def sentence(self) -> str:
        """One plain sentence about this program and where its models run."""
        if not self.models:
            return f"{self.program} answered at {self.base_url} and listed no models."
        listed = f"{self.program} answered at {self.base_url} with {len(self.models)} model(s)"
        here = len(self.here)
        if here == len(self.models):
            return f"{listed}, all of them on this machine."
        if not here:
            return f"{listed}, none of them on this machine."
        return f"{listed}: {here} on this machine and {len(self.models) - here} in the cloud."

    def lines(self) -> tuple[str, ...]:
        """One plain line per model, naming where it runs and why."""
        return tuple(home.sentence() for home in self.homes)


@dataclass(frozen=True)
class DiscoveryResult:
    """What a discovery found, what it tried, and what went wrong."""

    found: tuple[Found, ...] = ()
    tried: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when at least one local program answered."""
        return bool(self.found)

    def providers(self) -> tuple[Provider, ...]:
        """Every program that answered, as a provider row."""
        return tuple(item.as_provider() for item in self.found)

    def sentence(self) -> str:
        """One plain sentence, which names what it tried when it found nothing."""
        if self.found:
            asked = ", ".join(item.sentence() for item in self.found)
            if self.problems:
                return f"{asked} Not found: {'; '.join(self.problems)}"
            return asked
        tried = ", ".join(self.tried) if self.tried else "no addresses"
        if self.problems:
            return f"No local model program answered. Tried {tried}. {'; '.join(self.problems)}"
        return f"No local model program answered. Tried {tried}."


def discover(
    client: httpx.Client,
    *,
    endpoints: Sequence[LocalEndpoint] = LOCAL_ENDPOINTS,
    hosts: Sequence[str] = LOOPBACK_HOSTS,
) -> DiscoveryResult:
    """Ask every local program on the loopback address what models it holds.

    A program that answers with a list is a program the user can pick; one that
    does not answer is named in ``problems`` with the reason its own server
    gave. A program that answered at its first address is not asked at its
    second: 127.0.0.1 and [::1] are two spellings of this machine, and a server
    listening on both is one program and not two. No model is loaded and none
    is unloaded (spec 11.8).
    """
    found: list[Found] = []
    tried: list[str] = []
    problems: list[str] = []
    for endpoint in endpoints:
        for host in hosts:
            url = endpoint.url(host)
            tried.append(url)
            entries, problem = _ask(client, url, endpoint)
            if problem:
                problems.append(f"{url} ({problem})")
                continue
            found.append(
                Found(
                    program=endpoint.program,
                    base_url=endpoint.base_url(host),
                    models=tuple(name for name, _entry in entries),
                    form=endpoint.form,
                    homes=tuple(
                        model_home(name, program=endpoint.program, entry=entry)
                        for name, entry in entries
                    ),
                )
            )
            break
    return DiscoveryResult(found=tuple(found), tried=tuple(tried), problems=tuple(problems))


def endpoint_for(base_url: str) -> LocalEndpoint:
    """The local endpoint a provider's saved address points at.

    A saved address carries a host and a port but no path, so this reads the
    port out of it and finds the program that listens there. An address on a
    port no known program uses still gets an endpoint, with the OpenAI list
    path, because the user may have moved a program to a port of their own and
    the list request is the same either way.
    """
    port = httpx.URL(base_url).port or 0
    for endpoint in LOCAL_ENDPOINTS:
        if endpoint.port == port:
            return endpoint
    return LocalEndpoint("local", port, "/v1/models", FORM_OPENAI)


def probe(client: httpx.Client, base_url: str) -> tuple[bool, str]:
    """Ask one saved local address for its model list. Return yes/no and why.

    This is the reachability half of the preflight: the same read-only list
    request a discovery makes, on the address the user saved, so a local
    provider that has been stopped since it was added is found before a job
    starts rather than in the middle of one. No model is loaded (spec 11.8).
    """
    endpoint = endpoint_for(base_url)
    host = httpx.URL(base_url).host or "127.0.0.1"
    url = endpoint.url(host)
    _entries, problem = _ask(client, url, endpoint)
    if problem:
        return False, f"{url} ({problem})"
    return True, ""


def _ask(
    client: httpx.Client, url: str, endpoint: LocalEndpoint
) -> tuple[tuple[tuple[str, Mapping[str, Any]], ...], str]:
    """Ask one address for its model list. Return the entries, or the reason.

    An entry is the name and the whole raw reply item it was read from, because
    the item says where that model runs and the name alone does not.
    """
    _read_only_url(url)
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        return (), f"it did not answer ({exc.__class__.__name__})"
    if response.status_code != httpx.codes.OK:
        return (), f"it answered HTTP {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return (), "it answered with something that is not JSON"
    try:
        return _model_entries(payload, endpoint.form), ""
    except ValueError as exc:
        return (), str(exc)


def _model_entries(payload: Any, form: str) -> tuple[tuple[str, Mapping[str, Any]], ...]:
    """Read each model's name out of one program's list reply, with its entry."""
    if not isinstance(payload, Mapping):
        raise ValueError("it answered with JSON that is not an object")
    if form == FORM_OLLAMA:
        items = payload.get("models", [])
        keys = ("name", "model")
    else:
        items = payload.get("data", [])
        keys = ("id", "name")
    if not isinstance(items, list):
        raise ValueError("it answered with a list this program cannot read")
    entries: list[tuple[str, Mapping[str, Any]]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for key in keys:
            value = str(item.get(key, "") or "").strip()
            if value:
                entries.append((value, item))
                break
    return tuple(entries)


def _read_only_url(url: str) -> None:
    """Refuse any address that would load or unload a model (spec 11.8)."""
    path = httpx.URL(url).path
    if path not in READ_ONLY_PATHS:
        raise ValueError(
            f"{path} is not a list. A discovery only asks for a model list, because it never "
            "loads or unloads a model (spec 11.8)."
        )
