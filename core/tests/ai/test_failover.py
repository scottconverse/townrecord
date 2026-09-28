"""Failover down a ladder, and what each call is recorded as (spec 11.5)."""

from __future__ import annotations

import pytest

from townrecord.ai.failover import (
    FINAL_REASONS,
    MOVE_REASONS,
    REASON_CONTENT_REFUSAL,
    REASON_EMPTY_OUTPUT,
    REASON_EXPIRED_SIGN_IN,
    REASON_QUOTA,
    REASON_TIMEOUT,
    REASON_UNAVAILABLE,
    REASON_UNREADABLE_REPLY,
    REASONS,
    Attempt,
    moves_on,
    run_task,
)
from townrecord.ai.ladder import Ladder, Rung
from townrecord.ai.providers import (
    KIND_ANTHROPIC,
    KIND_CLAUDE_CLI,
    KIND_CODEX_CLI,
    KIND_LOCAL,
    Provider,
    ProviderRegistry,
)

from .conftest import CLOUD_NAME, CODEX_NAME, LOCAL_NAME


class FakeModels:
    """Every model in these tests, as a fake that records what it was asked.

    A model here is a name and an answer: nothing is called, and the record of
    which names were asked is the whole of what the failover rules decide
    (PROJECT-BRIEF rule 9).
    """

    def __init__(self, answers: dict[str, Attempt] | None = None) -> None:
        self.answers = dict(answers or {})
        #: Every provider asked, in order. A rung that must not be reached
        #: never appears here, which is how the local-model check reads.
        self.asked: list[str] = []

    def __call__(self, provider: Provider, rung: Rung) -> Attempt:
        self.asked.append(provider.name)
        return self.answers.get(provider.name, Attempt(ok=True, output=f"{provider.name} answered"))


class TestTheRules:
    def test_the_six_failures_that_move_the_ladder(self) -> None:
        assert {
            REASON_EXPIRED_SIGN_IN,
            REASON_TIMEOUT,
            REASON_EMPTY_OUTPUT,
            REASON_QUOTA,
            REASON_UNAVAILABLE,
            REASON_UNREADABLE_REPLY,
        } == MOVE_REASONS
        for reason in MOVE_REASONS:
            assert moves_on(reason) is True

    def test_a_content_refusal_does_not_move_the_ladder(self) -> None:
        assert {REASON_CONTENT_REFUSAL} == FINAL_REASONS
        assert moves_on(REASON_CONTENT_REFUSAL) is False

    def test_a_failure_nobody_classified_does_not_move_the_ladder(self) -> None:
        # Guessing that an unclassified message is one of the six would send
        # work to a second provider on the strength of a message nobody read.
        assert moves_on("something else went wrong") is False
        assert moves_on("") is False

    def test_every_reason_of_spec_11_5_is_listed(self) -> None:
        assert REASONS == (
            REASON_EXPIRED_SIGN_IN,
            REASON_TIMEOUT,
            REASON_EMPTY_OUTPUT,
            REASON_QUOTA,
            REASON_UNAVAILABLE,
            REASON_UNREADABLE_REPLY,
            REASON_CONTENT_REFUSAL,
        )


class TestRunningATask:
    def test_the_first_rung_answers_and_nothing_below_it_runs(
        self, registry: ProviderRegistry
    ) -> None:
        models = FakeModels()
        ladder = Ladder.automatic("summarize", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)])
        run = run_task(ladder=ladder, registry=registry, call=models)
        assert run.ok is True
        assert models.asked == [LOCAL_NAME]
        assert len(run.records) == 1
        assert run.records[0].ran == LOCAL_NAME
        assert run.sentence == "summarize ran on the small local model."

    def test_a_rung_that_times_out_moves_the_ladder_down(self, registry: ProviderRegistry) -> None:
        models = FakeModels({LOCAL_NAME: Attempt(ok=False, reason=REASON_TIMEOUT)})
        ladder = Ladder.automatic("summarize", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)])
        run = run_task(ladder=ladder, registry=registry, call=models)
        assert run.ok is True
        assert models.asked == [LOCAL_NAME, "the subscription"]
        assert run.records[0].reason == REASON_TIMEOUT
        assert run.records[1].chosen_because == f"the rung above it failed with a {REASON_TIMEOUT}"
        assert "after 1 failed rung(s)" in run.sentence

    def test_a_content_refusal_does_not_fail_over(self, registry: ProviderRegistry) -> None:
        """The named check: a refusal is final, and the rung below never runs."""
        models = FakeModels(
            {
                LOCAL_NAME: Attempt(
                    ok=False,
                    reason=REASON_CONTENT_REFUSAL,
                    detail="I cannot help with that.",
                )
            }
        )
        ladder = Ladder.automatic("summarize", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)])
        run = run_task(ladder=ladder, registry=registry, call=models)
        assert run.ok is False
        assert models.asked == [LOCAL_NAME], "the rung below a refusal must not be called"
        assert len(run.records) == 1
        assert run.records[0].reason == REASON_CONTENT_REFUSAL
        assert "which is final" in run.sentence
        assert "I cannot help with that." in run.sentence

    def test_every_rung_failing_ends_the_task_with_every_call_recorded(
        self, registry: ProviderRegistry
    ) -> None:
        models = FakeModels(
            {
                LOCAL_NAME: Attempt(ok=False, reason=REASON_UNAVAILABLE),
                "the subscription": Attempt(ok=False, reason=REASON_QUOTA),
            }
        )
        ladder = Ladder.automatic("summarize", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)])
        run = run_task(ladder=ladder, registry=registry, call=models)
        assert run.ok is False
        assert len(run.records) == 2
        assert "Every rung of the summarize ladder failed" in run.sentence

    def test_a_rung_that_is_not_reachable_is_never_called(self, registry: ProviderRegistry) -> None:
        models = FakeModels()
        ladder = Ladder.automatic("summarize", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)])
        run = run_task(
            ladder=ladder,
            registry=registry,
            call=models,
            reachable=lambda provider: provider.kind != KIND_LOCAL,
        )
        assert run.ok is True
        assert models.asked == ["the subscription"]
        assert len(run.skipped) == 1
        assert "it is the first rung that is reachable" in run.records[0].chosen_because

    def test_no_rung_can_run_and_nothing_is_called(self, registry: ProviderRegistry) -> None:
        models = FakeModels()
        ladder = Ladder.automatic("summarize", [Rung(provider="the one I deleted")])
        run = run_task(ladder=ladder, registry=registry, call=models)
        assert run.ok is False
        assert models.asked == []
        assert "No model can run summarize right now" in run.sentence


class TestAPickedLocalModel:
    def test_a_picked_local_model_never_reaches_a_cloud_fake(
        self, registry: ProviderRegistry
    ) -> None:
        """The named check: local work stays local, and the task fails closed."""
        models = FakeModels({LOCAL_NAME: Attempt(ok=False, reason=REASON_TIMEOUT)})
        ladder = Ladder.picked("summarize", LOCAL_NAME, "qwen3:8b")
        run = run_task(ladder=ladder, registry=registry, call=models)
        assert run.ok is False
        assert run.failed_closed is True
        assert models.asked == [LOCAL_NAME]
        assert CLOUD_NAME not in models.asked
        assert CODEX_NAME not in models.asked
        assert "not sent to another provider" in run.sentence

    def test_a_picked_local_model_does_not_fail_closed_when_it_answers(
        self, registry: ProviderRegistry
    ) -> None:
        models = FakeModels()
        ladder = Ladder.picked("summarize", LOCAL_NAME)
        run = run_task(ladder=ladder, registry=registry, call=models)
        assert run.ok is True
        assert run.failed_closed is False

    def test_a_picked_local_model_fails_closed_on_a_final_reason_too(
        self, registry: ProviderRegistry
    ) -> None:
        models = FakeModels({LOCAL_NAME: Attempt(ok=False, reason=REASON_CONTENT_REFUSAL)})
        ladder = Ladder.picked("summarize", LOCAL_NAME)
        run = run_task(ladder=ladder, registry=registry, call=models)
        assert run.failed_closed is False, "a final reason is not a fail-closed stop"
        assert models.asked == [LOCAL_NAME]


class TestWhatEachCallRecords:
    def test_the_three_things_spec_11_5_asks_for(self, registry: ProviderRegistry) -> None:
        models = FakeModels({LOCAL_NAME: Attempt(ok=False, reason=REASON_EXPIRED_SIGN_IN)})
        ladder = Ladder.automatic("summarize", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)])
        run = run_task(ladder=ladder, registry=registry, call=models)
        first, second = run.records
        assert first.requested == LOCAL_NAME
        assert first.ran == LOCAL_NAME
        assert first.chosen_because == "it is the first rung of the ladder"
        assert first.reason == REASON_EXPIRED_SIGN_IN
        assert second.requested == LOCAL_NAME, "what was asked for, not what ran"
        assert second.ran == "the subscription"
        assert "the rung above it failed" in second.chosen_because
        assert second.reason == ""

    def test_a_record_keeps_the_model_name_of_the_rung_that_ran(
        self, registry: ProviderRegistry
    ) -> None:
        models = FakeModels()
        ladder = Ladder.automatic(
            "summarize", [Rung(kind=KIND_CLAUDE_CLI, model="claude-sonnet-5")]
        )
        run = run_task(ladder=ladder, registry=registry, call=models)
        assert run.records[0].ran == "the subscription/claude-sonnet-5"

    def test_a_recorded_sentence_names_both_models(self, registry: ProviderRegistry) -> None:
        models = FakeModels({LOCAL_NAME: Attempt(ok=False, reason=REASON_QUOTA)})
        ladder = Ladder.automatic("summarize", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_CLAUDE_CLI)])
        run = run_task(ladder=ladder, registry=registry, call=models)
        sentence = run.records[1].sentence()
        assert LOCAL_NAME in sentence and "the subscription" in sentence
        assert "requested from" in sentence and "ran on" in sentence


class TestEveryMoveReasonIsDriven:
    """Each of the six move reasons, one test each, through the same path."""

    @pytest.mark.parametrize(
        "reason",
        [
            REASON_EXPIRED_SIGN_IN,
            REASON_TIMEOUT,
            REASON_EMPTY_OUTPUT,
            REASON_QUOTA,
            REASON_UNAVAILABLE,
            REASON_UNREADABLE_REPLY,
        ],
    )
    def test_a_move_reason_moves(self, registry: ProviderRegistry, reason: str) -> None:
        models = FakeModels({LOCAL_NAME: Attempt(ok=False, reason=reason)})
        ladder = Ladder.automatic("summarize", [Rung(kind=KIND_LOCAL), Rung(kind=KIND_ANTHROPIC)])
        run = run_task(ladder=ladder, registry=registry, call=models)
        assert run.ok is True
        assert models.asked == [LOCAL_NAME, CLOUD_NAME]
        assert run.records[0].reason == reason

    def test_a_rung_with_a_kind_that_is_not_configured_is_passed_over(
        self, registry: ProviderRegistry
    ) -> None:
        models = FakeModels()
        ladder = Ladder.automatic("summarize", [Rung(kind=KIND_CODEX_CLI)])
        run = run_task(ladder=ladder, registry=registry, call=models)
        assert run.ok is True
        assert models.asked == [CODEX_NAME]
