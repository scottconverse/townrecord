"""Tasks, ladders and the rung that runs (spec 11.4)."""

from __future__ import annotations

import pytest

from townrecord.ai.ladder import (
    AUTOMATIC_LABEL,
    DEFAULT_LADDERS,
    MODE_AUTOMATIC,
    MODE_PROVIDER,
    TASKS,
    Ladder,
    Rung,
    default_rungs,
)
from townrecord.ai.providers import (
    KIND_ANTHROPIC,
    KIND_CLAUDE_CLI,
    KIND_CODEX_CLI,
    KIND_LOCAL,
    Provider,
    ProviderRegistry,
)

from .conftest import CLOUD_NAME, LOCAL_NAME


class TestTheTasks:
    def test_the_eight_tasks_of_spec_11_4_are_here(self) -> None:
        assert TASKS == (
            "classify",
            "discover",
            "align",
            "summarize",
            "answer",
            "fact-check",
            "ocr",
            "embed",
        )

    def test_every_task_has_a_built_in_ladder(self) -> None:
        assert set(DEFAULT_LADDERS) == set(TASKS)
        for task in TASKS:
            assert default_rungs(task), f"{task} has no built-in rung"

    def test_transcribe_is_not_a_task_of_this_registry(self) -> None:
        # It is TextFlowKit on this machine, not a model in this registry.
        assert "transcribe" not in TASKS
        with pytest.raises(ValueError, match="not a task"):
            Ladder.automatic("transcribe", (Rung(kind=KIND_LOCAL),))

    def test_a_built_in_ladder_names_kinds_rather_than_invented_providers(self) -> None:
        for task, rungs in DEFAULT_LADDERS.items():
            assert all(rung.kind for rung in rungs), task
            assert all(not rung.provider for rung in rungs), task

    def test_ocr_and_embedding_never_leave_the_machine(self) -> None:
        assert [rung.kind for rung in default_rungs("ocr")] == [KIND_LOCAL]
        assert [rung.kind for rung in default_rungs("embed")] == [KIND_LOCAL]

    def test_a_ladder_with_no_rungs_is_refused(self) -> None:
        with pytest.raises(ValueError, match="has no rungs"):
            Ladder.automatic("classify", ())

    def test_a_rung_names_a_provider_or_a_kind(self) -> None:
        with pytest.raises(ValueError, match="names a provider or a provider kind"):
            Rung()
        with pytest.raises(ValueError, match="not a provider kind"):
            Rung(kind="magic")


class TestResolvingALadder:
    def test_the_first_rung_runs_when_it_is_reachable(self, registry: ProviderRegistry) -> None:
        ladder = Ladder.automatic("classify", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)])
        found = ladder.resolve(registry)
        assert found.ok is True
        assert found.index == 0
        assert found.provider is not None and found.provider.name == LOCAL_NAME
        assert found.skipped == ()

    def test_a_ladder_skips_an_unreachable_rung(self, registry: ProviderRegistry) -> None:
        """The named check: an unreachable rung is passed over, not run."""
        ladder = Ladder.automatic("summarize", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)])
        asked: list[str] = []

        def reachable(provider: Provider) -> bool:
            asked.append(provider.name)
            return provider.kind != KIND_LOCAL

        found = ladder.resolve(registry, reachable=reachable)
        assert found.ok is True
        assert found.index == 1
        assert found.provider is not None and found.provider.kind == KIND_CLAUDE_CLI
        # The unreachable rung was asked about exactly once, and the rung that
        # ran is the one below it.
        assert asked == [LOCAL_NAME, "the subscription"]
        assert len(found.skipped) == 1
        assert "not reachable" in found.skipped[0]

    def test_a_ladder_of_unreachable_rungs_runs_nothing(self, registry: ProviderRegistry) -> None:
        ladder = Ladder.automatic("summarize", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)])
        found = ladder.resolve(registry, reachable=lambda provider: False)
        assert found.ok is False
        assert found.index is None
        assert found.provider is None
        assert len(found.skipped) == 2

    def test_a_rung_naming_a_provider_that_is_not_configured_is_skipped(
        self, registry: ProviderRegistry
    ) -> None:
        ladder = Ladder.automatic(
            "classify", [Rung(provider="the one I deleted"), Rung(kind=KIND_LOCAL)]
        )
        found = ladder.resolve(registry)
        assert found.index == 1
        assert "is not configured" in found.skipped[0]

    def test_a_rung_that_names_a_provider_takes_that_one(self, registry: ProviderRegistry) -> None:
        ladder = Ladder.automatic("classify", [Rung(provider=CLOUD_NAME, model="claude-sonnet-5")])
        found = ladder.resolve(registry)
        assert found.provider is not None and found.provider.name == CLOUD_NAME
        assert "the paid API" in found.sentence()
        assert "claude-sonnet-5" in found.sentence()

    def test_the_resolution_says_what_it_skipped(self, registry: ProviderRegistry) -> None:
        ladder = Ladder.automatic("classify", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)])
        found = ladder.resolve(registry, reachable=lambda provider: provider.kind != KIND_LOCAL)
        assert "after skipping" in found.sentence()
        assert LOCAL_NAME in found.sentence()


class TestAPickedProvider:
    def test_a_picked_setting_has_one_rung(self, registry: ProviderRegistry) -> None:
        ladder = Ladder.picked("answer", CLOUD_NAME, "claude-sonnet-5")
        assert ladder.mode == MODE_PROVIDER
        assert ladder.is_automatic is False
        assert len(ladder.rungs) == 1
        assert ladder.resolve(registry).provider is not None

    def test_a_picked_local_setting_fails_closed(self, registry: ProviderRegistry) -> None:
        assert Ladder.picked("answer", LOCAL_NAME).fails_closed(registry) is True

    def test_a_picked_local_model_that_runs_here_fails_closed(
        self, registry: ProviderRegistry
    ) -> None:
        """The model is asked, and this one runs on this machine."""
        assert Ladder.picked("answer", LOCAL_NAME, "qwen3:8b").fails_closed(registry) is True

    def test_a_picked_cloud_model_on_a_local_row_does_not_fail_closed(
        self, registry: ProviderRegistry
    ) -> None:
        """A cloud model behind a local program is not local work (spec 11.5).

        Failing closed is what a local model does after it fails. This one is
        refused before it is called at all, which
        ``tests/ai/test_failover.py`` checks.
        """
        ladder = Ladder.picked("summarize", LOCAL_NAME, "kimi-k2.6:cloud")
        assert ladder.fails_closed(registry) is False

    def test_a_picked_model_no_list_named_is_not_local(self, registry: ProviderRegistry) -> None:
        ladder = Ladder.picked("summarize", LOCAL_NAME, "a-model-nobody-listed")
        assert ladder.fails_closed(registry) is False

    def test_a_picked_cloud_setting_does_not_fail_closed(self, registry: ProviderRegistry) -> None:
        assert Ladder.picked("answer", CLOUD_NAME).fails_closed(registry) is False

    def test_an_automatic_ladder_never_fails_closed(self, registry: ProviderRegistry) -> None:
        ladder = Ladder.automatic("answer", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CODEX_CLI)])
        assert ladder.fails_closed(registry) is False

    def test_an_unfindable_local_rung_still_fails_closed(self) -> None:
        # The safest reading of a local rung we cannot find is that it is local.
        ladder = Ladder.picked("answer", "the one I deleted")
        assert ladder.fails_closed(ProviderRegistry()) is True

    def test_the_label_a_user_sees_is_automatic(self) -> None:
        assert AUTOMATIC_LABEL == "Automatic"
        assert Ladder.automatic("classify", [Rung(kind=KIND_LOCAL)]).mode == MODE_AUTOMATIC


class TestStoredLadders:
    def test_a_stored_ladder_reads_back(self) -> None:
        ladder = Ladder.from_json(
            "answer",
            {"mode": MODE_AUTOMATIC, "ladder": [{"kind": KIND_CLAUDE_CLI, "model": "m"}]},
        )
        assert ladder.is_automatic is True
        assert ladder.first().kind == KIND_CLAUDE_CLI
        assert ladder.first().model == "m"

    def test_a_stored_picked_setting_reads_back(self) -> None:
        ladder = Ladder.from_json(
            "answer", {"mode": MODE_PROVIDER, "provider": "mine", "model": "m"}
        )
        assert ladder.is_automatic is False
        assert ladder.first().provider == "mine"

    def test_an_empty_stored_ladder_is_the_built_in_one(self) -> None:
        ladder = Ladder.from_json("answer", {"ladder": []})
        assert ladder.rungs == default_rungs("answer")

    def test_a_picked_setting_with_no_provider_is_refused(self) -> None:
        with pytest.raises(ValueError, match="names the provider"):
            Ladder.from_json("answer", {"mode": MODE_PROVIDER})

    def test_a_rung_round_trips_through_its_stored_form(self) -> None:
        rung = Rung(provider="mine", model="m", kind=KIND_ANTHROPIC)
        assert Rung.from_json(rung.as_json()) == rung
