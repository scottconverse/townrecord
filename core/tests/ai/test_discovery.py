"""Finding the local model programs and listing what they hold (spec 11.1, 11.8)."""

from __future__ import annotations

import httpx
import pytest

from townrecord.ai import discovery
from townrecord.ai.discovery import (
    LOCAL_ENDPOINTS,
    LOOPBACK_HOSTS,
    READ_ONLY_PATHS,
    Found,
    discover,
    endpoint_for,
    probe,
)
from townrecord.ai.providers import KIND_LOCAL, RUNS_HERE, cloud_label

from .conftest import (
    RECORDED_CLOUD,
    RECORDED_HERE,
    RECORDED_OLLAMA_TAGS,
    FakeLocalServer,
    ollama_body,
    openai_body,
)


class TestWhatItAsksFor:
    def test_the_three_programs_of_spec_11_1_are_tried_at_their_default_ports(self) -> None:
        ports = {endpoint.program: endpoint.port for endpoint in LOCAL_ENDPOINTS}
        assert ports == {"ollama": 11434, "lmstudio": 1234, "llamacpp": 8080}

    def test_every_program_is_tried_on_both_loopback_spellings(self) -> None:
        assert LOOPBACK_HOSTS == ("127.0.0.1", "[::1]")

    def test_a_discovery_asks_only_for_a_model_list(self, local_server: FakeLocalServer) -> None:
        local_server.replies = {11434: ollama_body("qwen3:8b")}
        discover(local_server.client())
        assert set(local_server.paths()) == {"/api/tags", "/v1/models"}
        assert set(local_server.paths()) <= READ_ONLY_PATHS

    def test_a_discovery_never_calls_a_load_or_unload_endpoint(
        self, local_server: FakeLocalServer
    ) -> None:
        """The named check: no request would load or unload a model (spec 11.8)."""
        local_server.replies = {
            11434: ollama_body("qwen3:8b"),
            1234: openai_body("lmstudio-community/qwen3"),
            8080: openai_body("a.gguf"),
        }
        discover(local_server.client())
        assert local_server.requests, "the fake server was never asked anything"
        forbidden = (
            "/api/pull",
            "/api/push",
            "/api/generate",
            "/api/chat",
            "/api/delete",
            "/load",
            "/unload",
        )
        for path in local_server.paths():
            assert path in READ_ONLY_PATHS
            assert not any(word in path for word in forbidden)
        # A load is a POST, and every request here is a GET.
        assert set(local_server.methods()) == {"GET"}

    def test_the_guard_refuses_anything_that_is_not_a_list(self) -> None:
        for url in (
            "http://127.0.0.1:11434/api/pull",
            "http://127.0.0.1:11434/api/generate",
            "http://127.0.0.1:1234/v1/completions",
            "http://127.0.0.1:8080/v1/chat/completions",
        ):
            with pytest.raises(ValueError, match="never loads or unloads"):
                discovery._read_only_url(url)
        discovery._read_only_url("http://127.0.0.1:11434/api/tags")


class TestFinding:
    def test_ollama_is_found_and_its_models_are_listed(self, local_server: FakeLocalServer) -> None:
        local_server.replies = {11434: ollama_body("qwen3:8b", "llama3.2:3b")}
        result = discover(local_server.client())
        assert result.ok is True
        found = result.found[0]
        assert found.program == "ollama"
        assert found.models == ("qwen3:8b", "llama3.2:3b")
        assert found.base_url == "http://127.0.0.1:11434"
        assert "2 model(s)" in found.sentence()

    def test_lm_studio_is_found_from_the_openai_shape(self, local_server: FakeLocalServer) -> None:
        local_server.replies = {1234: openai_body("qwen3-8b")}
        result = discover(local_server.client())
        assert [item.program for item in result.found] == ["lmstudio"]
        assert result.found[0].models == ("qwen3-8b",)

    def test_a_program_that_answers_the_first_address_is_not_asked_the_second(
        self, local_server: FakeLocalServer
    ) -> None:
        local_server.replies = {11434: ollama_body("qwen3:8b")}
        discover(local_server.client(), endpoints=LOCAL_ENDPOINTS[:1])
        assert local_server.paths() == ["/api/tags"]

    def test_a_found_program_becomes_a_provider_row(self, local_server: FakeLocalServer) -> None:
        local_server.replies = {11434: ollama_body("qwen3:8b")}
        provider = discover(local_server.client()).providers()[0]
        assert provider.kind == KIND_LOCAL
        assert provider.name == "ollama"
        assert provider.base_url == "http://127.0.0.1:11434"

    def test_the_provider_name_a_caller_gives_is_the_one_used(
        self, local_server: FakeLocalServer
    ) -> None:
        local_server.replies = {11434: ollama_body("qwen3:8b")}
        found = discover(local_server.client()).found[0]
        assert found.as_provider("the small one").name == "the small one"


class TestWhereEachModelRuns:
    """A listing says where each model runs, and why (spec 2, 11.5).

    Ollama answers for both the models it holds on this machine and the ones it
    serves from its own cloud, and the two are listed side by side. A user
    shown only names would pick one of the cloud ones believing it runs here.
    """

    def test_a_cloud_model_on_the_local_program_is_not_local(
        self, local_server: FakeLocalServer
    ) -> None:
        """The named check: the recorded reply, read model by model."""
        local_server.replies = {11434: RECORDED_OLLAMA_TAGS}
        found = discover(local_server.client()).found[0]
        assert found.models == (*RECORDED_HERE, *RECORDED_CLOUD), "every model is still listed"
        assert [home.model for home in found.here] == list(RECORDED_HERE)
        cloud = [home for home in found.homes if not home.is_local]
        assert [home.model for home in cloud] == list(RECORDED_CLOUD)
        assert {home.runs_on for home in cloud} == {cloud_label("ollama")} == {"cloud (via Ollama)"}
        assert "2 on this machine and 2 in the cloud" in found.sentence()

    def test_every_line_names_where_that_model_runs(self, local_server: FakeLocalServer) -> None:
        local_server.replies = {11434: RECORDED_OLLAMA_TAGS}
        found = discover(local_server.client()).found[0]
        lines = found.lines()
        assert len(lines) == len(found.models)
        for line, home in zip(lines, found.homes, strict=True):
            assert line == home.sentence()
        assert "runs on this machine" in lines[0]
        assert "the form Ollama gives a model it runs in its own cloud" in lines[2]
        assert all(line.endswith(".") for line in lines)

    def test_a_program_whose_models_all_run_here_says_so(
        self, local_server: FakeLocalServer
    ) -> None:
        local_server.replies = {11434: ollama_body("qwen3:8b", "llama3.2:3b")}
        found = discover(local_server.client()).found[0]
        assert found.here == found.homes
        assert "all of them on this machine" in found.sentence()

    def test_a_program_with_nothing_on_this_machine_says_that_too(
        self, local_server: FakeLocalServer
    ) -> None:
        cloud_only = {
            "models": [
                entry
                for entry in RECORDED_OLLAMA_TAGS["models"]
                if str(entry["name"]) in RECORDED_CLOUD
            ]
        }
        local_server.replies = {11434: cloud_only}
        found = discover(local_server.client()).found[0]
        assert found.here == ()
        assert "none of them on this machine" in found.sentence()

    def test_the_other_programs_models_are_on_this_machine(
        self, local_server: FakeLocalServer
    ) -> None:
        local_server.replies = {1234: openai_body("qwen3-8b")}
        found = discover(local_server.client()).found[0]
        assert found.program == "lmstudio"
        assert found.here == found.homes
        assert found.homes[0].runs_on == RUNS_HERE

    def test_the_row_a_listing_becomes_keeps_the_same_answer(
        self, local_server: FakeLocalServer
    ) -> None:
        """The saved row holds names only, and the name is the answer for those."""
        local_server.replies = {11434: RECORDED_OLLAMA_TAGS}
        provider = discover(local_server.client()).providers()[0]
        assert provider.models == (*RECORDED_HERE, *RECORDED_CLOUD)
        assert provider.is_local(RECORDED_CLOUD[0]) is False
        assert provider.is_local(RECORDED_HERE[0]) is True

    def test_a_found_built_from_names_alone_still_reads_the_name(self) -> None:
        found = Found(
            program="ollama",
            base_url="http://127.0.0.1:11434",
            models=("kimi-k2.6:cloud", "a-model-nobody-listed"),
        )
        assert found.here == ()
        assert [home.runs_on for home in found.homes] == [cloud_label("ollama")] * 2


class TestWhenNothingAnswers:
    def test_a_discovery_that_finds_nothing_says_what_it_tried(self) -> None:
        server = FakeLocalServer()
        result = discover(server.client())
        assert result.ok is False
        assert result.found == ()
        assert len(result.tried) == len(LOCAL_ENDPOINTS) * len(LOOPBACK_HOSTS)
        assert "127.0.0.1:11434" in result.sentence()
        assert "No local model program answered. Tried" in result.sentence()

    def test_a_found_program_with_a_quiet_neighbour_names_both(
        self, local_server: FakeLocalServer
    ) -> None:
        local_server.replies = {11434: ollama_body("qwen3:8b")}
        result = discover(local_server.client())
        assert result.ok is True
        assert result.problems
        assert "Not found:" in result.sentence()

    def test_a_server_that_answers_with_a_status_is_reported_by_it(self) -> None:
        server = FakeLocalServer({11434: 500})
        result = discover(server.client())
        assert result.ok is False
        assert "HTTP 500" in result.sentence()

    def test_a_server_that_answers_with_something_else_is_reported(
        self, local_server: FakeLocalServer
    ) -> None:
        local_server.replies = {11434: "this is not a model list"}
        result = discover(local_server.client())
        assert result.ok is False
        assert "127.0.0.1:11434/api/tags (it answered with JSON that is not an object)" in (
            result.sentence()
        )

    def test_a_list_of_a_shape_this_program_cannot_read_is_reported(
        self, local_server: FakeLocalServer
    ) -> None:
        local_server.replies = {11434: {"models": "one model, honestly"}}
        result = discover(local_server.client())
        assert result.ok is False
        assert "a list this program cannot read" in result.sentence()

    def test_a_refused_connection_is_named_as_that(self) -> None:
        result = discover(FakeLocalServer().client())
        assert "ConnectError" in result.sentence()


class TestProbingOneAddress:
    def test_a_saved_local_address_that_answers_is_reachable(
        self, local_server: FakeLocalServer
    ) -> None:
        local_server.replies = {11434: ollama_body("qwen3:8b")}
        answered, reason = probe(local_server.client(), "http://127.0.0.1:11434")
        assert answered is True
        assert reason == ""
        assert local_server.paths() == ["/api/tags"]

    def test_a_saved_local_address_that_does_not_answer_says_why(self) -> None:
        answered, reason = probe(FakeLocalServer().client(), "http://127.0.0.1:11434")
        assert answered is False
        assert "127.0.0.1:11434/api/tags" in reason

    def test_the_endpoint_is_read_from_the_port_the_user_saved(self) -> None:
        assert endpoint_for("http://127.0.0.1:11434").program == "ollama"
        assert endpoint_for("http://127.0.0.1:11434").list_path == "/api/tags"
        assert endpoint_for("http://127.0.0.1:1234").program == "lmstudio"
        assert endpoint_for("http://127.0.0.1:9999").list_path == "/v1/models"

    def test_probing_a_port_this_version_does_not_know_still_only_lists(
        self, local_server: FakeLocalServer
    ) -> None:
        local_server.replies = {9999: openai_body("a-model")}
        answered, _reason = probe(local_server.client(), "http://127.0.0.1:9999")
        assert answered is True
        assert local_server.paths() == ["/v1/models"]

    def test_the_guard_refuses_a_delete_path(self) -> None:
        with pytest.raises(ValueError):
            discovery._read_only_url("http://127.0.0.1:11434/api/delete")


class TestTheTimeout:
    def test_the_probe_timeout_is_short_and_finite(self) -> None:
        # A wait longer than this is a program that is not the one we want: a
        # local server that is running answers at once, and one that is not
        # refuses at once.
        assert 0 < discovery.DEFAULT_PROBE_TIMEOUT_S <= 10.0

    def test_the_client_a_caller_hands_in_is_the_one_used(self) -> None:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=ollama_body("m"))

        client = httpx.Client(trust_env=False, transport=httpx.MockTransport(handle))
        discover(client, endpoints=LOCAL_ENDPOINTS[:1], hosts=("127.0.0.1",))
        assert len(seen) == 1
