"""Shared pieces for the model-layer tests.

No test calls a model (PROJECT-BRIEF rule 9). An HTTP provider is answered by
an ``httpx.MockTransport`` and a command-line provider is answered by
:class:`FakeRunner`, which is a stand-in for
:func:`townrecord.proc.run_allowlisted` that records the argument lists it was
asked to run and answers from a table the test wrote.

Every address here is a loopback address or a name that cannot resolve. Nothing
in this folder reaches a vendor, and :func:`offline_client` turns off the
machine's proxy settings so a test never depends on the machine it runs on
(PROJECT-BRIEF rule 11b: the same tests run on Windows, macOS and Linux).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx
import pytest

from townrecord import proc
from townrecord.ai.providers import (
    KIND_ANTHROPIC,
    KIND_CLAUDE_CLI,
    KIND_CODEX_CLI,
    KIND_LOCAL,
    KIND_OPENAI_COMPATIBLE,
    Provider,
    ProviderRegistry,
)

#: A key with the shape a real one has, so the redaction tests are about the
#: shape and not about a word that happens to be in a sentence. It is not a
#: key: no vendor issued it.
TEST_KEY = "sk-ant-test-0000000000000000"

#: The names the tests use for their providers. The user's own names are the
#: point of the registry, so nothing here is a program's name.
LOCAL_NAME = "the small local model"
CLOUD_NAME = "the paid API"
COMPATIBLE_NAME = "the gateway"
CLAUDE_NAME = "the subscription"
CODEX_NAME = "the other subscription"


def offline_client(**kwargs: Any) -> httpx.Client:
    """An httpx.Client that ignores the machine's proxy settings.

    A test must not inherit HTTP_PROXY from whoever runs it: on a machine whose
    proxy points at a closed port, httpx refuses to build the client at all,
    and the reading would be about a proxy rather than about this code.
    """
    return httpx.Client(trust_env=False, **kwargs)


class FakeRunner:
    """A stand-in for ``proc.run_allowlisted`` that runs nothing (rule 9).

    The program is never executed. What a test reads is the argument lists that
    were built, which is the whole of spec 11.3: the flags are the thing being
    pinned.
    """

    def __init__(self, *, returncode: int = 0, stdout: bytes = b"", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        #: Every argument list handed to the runner, in order.
        self.calls: list[tuple[str, ...]] = []
        #: Every timeout the caller asked for, in order.
        self.timeouts: list[float | None] = []
        #: Set to a result and it is returned as-is, which is how a test drives
        #: an answer that is not a plain return code.
        self.answer: proc.ProcessResult | None = None

    def __call__(
        self, argv: Sequence[str], *, timeout_s: float | None = None, cwd: str | None = None
    ) -> proc.ProcessResult:
        self.calls.append(tuple(str(part) for part in argv))
        self.timeouts.append(timeout_s)
        if self.answer is not None:
            return self.answer
        return proc.ProcessResult(
            argv=tuple(str(part) for part in argv),
            returncode=self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )

    def last(self) -> tuple[str, ...]:
        """The last argument list the runner was asked for."""
        assert self.calls, "the runner was never called"
        return self.calls[-1]


@pytest.fixture
def runner() -> FakeRunner:
    """A fake process runner: a call that would run a program runs nothing."""
    return FakeRunner()


@pytest.fixture
def local_provider() -> Provider:
    """A local model on the loopback address."""
    return Provider(
        name=LOCAL_NAME, kind=KIND_LOCAL, base_url="http://127.0.0.1:11434", models=("qwen3:8b",)
    )


@pytest.fixture
def cloud_provider() -> Provider:
    """A vendor API with a key."""
    return Provider(
        name=CLOUD_NAME,
        kind=KIND_ANTHROPIC,
        base_url="https://api.anthropic.com",
        api_key=TEST_KEY,
    )


@pytest.fixture
def claude_provider() -> Provider:
    """The claude subscription program."""
    return Provider(name=CLAUDE_NAME, kind=KIND_CLAUDE_CLI)


@pytest.fixture
def codex_provider() -> Provider:
    """The codex subscription program."""
    return Provider(name=CODEX_NAME, kind=KIND_CODEX_CLI)


@pytest.fixture
def gateway_provider() -> Provider:
    """An OpenAI-compatible gateway at the user's own address."""
    return Provider(
        name=COMPATIBLE_NAME, kind=KIND_OPENAI_COMPATIBLE, base_url="http://127.0.0.1:4000"
    )


@pytest.fixture
def registry(
    local_provider: Provider,
    cloud_provider: Provider,
    claude_provider: Provider,
    codex_provider: Provider,
    gateway_provider: Provider,
) -> ProviderRegistry:
    """Every kind of provider the tests use, in one registry."""
    return ProviderRegistry(
        [local_provider, cloud_provider, claude_provider, codex_provider, gateway_provider]
    )


class FakeLocalServer:
    """A fake Ollama or LM Studio, which records every request it answers.

    ``replies`` is keyed by port, so one test can have Ollama answer and LM
    Studio stay quiet. A value of :data:`SILENT` makes the request fail the way
    a stopped server fails: the connection is refused.
    """

    def __init__(self, replies: Mapping[int, Any] | None = None) -> None:
        self.replies: dict[int, Any] = dict(replies or {})
        #: Every request made, in order. The load-and-unload check reads this.
        self.requests: list[httpx.Request] = []

    def client(self, **kwargs: Any) -> httpx.Client:
        """A client whose every answer is this fake."""
        return offline_client(transport=httpx.MockTransport(self.handle), **kwargs)

    def paths(self) -> list[str]:
        """The request paths asked for, in order."""
        return [request.url.path for request in self.requests]

    def methods(self) -> list[str]:
        """The request methods used, in order."""
        return [request.method for request in self.requests]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        port = request.url.port or 0
        reply = self.replies.get(port, SILENT)
        if reply is SILENT:
            raise httpx.ConnectError("the connection was refused", request=request)
        if isinstance(reply, int):
            return httpx.Response(reply, request=request)
        return httpx.Response(200, json=reply, request=request)


#: A port that answers nothing, the way a program that is not running does not.
SILENT = object()


def ollama_body(*names: str) -> dict[str, Any]:
    """An Ollama ``/api/tags`` reply listing ``names``."""
    return {"models": [{"name": name, "model": name} for name in names]}


#: A recorded Ollama ``/api/tags`` reply, read over the loopback address with a
#: GET and nothing else. This is the shape the local-versus-cloud rule is read
#: from: the two models that run on this machine carry no ``remote_host``, and
#: the two Ollama serves from its own cloud carry ``remote_host`` and
#: ``remote_model`` beside a name in the ``:cloud`` or ``-cloud`` form. Both
#: halves of that are pinned by ``tests/ai/test_registry.py`` and
#: ``tests/ai/test_discovery.py``, so a change to the rule has to change what
#: was recorded here first.
#:
#: It is trimmed: four of the twenty-three entries the machine listed, with the
#: fields that show the shape and shortened values where the value is noise.
RECORDED_OLLAMA_TAGS: dict[str, Any] = {
    "models": [
        {
            "name": "qwen3.8:27b-q4_K_M",
            "model": "qwen3.8:27b-q4_K_M",
            "size": 17_200_000_000,
            "digest": "recorded",
            "format": "gguf",
        },
        {
            "name": "qwen3-coder:30b-a3b-q4_K_M",
            "model": "qwen3-coder:30b-a3b-q4_K_M",
            "size": 18_600_000_000,
            "digest": "recorded",
            "format": "gguf",
        },
        {
            "name": "kimi-k2.6:cloud",
            "model": "kimi-k2.6:cloud",
            "size": 310,
            "digest": "recorded",
            "remote_model": "kimi-k2.6",
            "remote_host": "https://ollama.com",
        },
        {
            "name": "deepseek-v4-pro:0813-cloud",
            "model": "deepseek-v4-pro:0813-cloud",
            "size": 310,
            "digest": "recorded",
            "remote_model": "deepseek-v4-pro:0813",
            "remote_host": "https://ollama.com",
        },
    ]
}

#: The models of :data:`RECORDED_OLLAMA_TAGS` that run on this machine, and the
#: ones Ollama serves from its own cloud.
RECORDED_HERE = ("qwen3.8:27b-q4_K_M", "qwen3-coder:30b-a3b-q4_K_M")
RECORDED_CLOUD = ("kimi-k2.6:cloud", "deepseek-v4-pro:0813-cloud")


def openai_body(*names: str) -> dict[str, Any]:
    """An OpenAI-compatible ``/v1/models`` reply listing ``names``."""
    return {"object": "list", "data": [{"id": name, "object": "model"} for name in names]}


@pytest.fixture
def local_server() -> FakeLocalServer:
    """A fake local model server with no replies configured yet."""
    return FakeLocalServer()
