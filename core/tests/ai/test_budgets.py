"""Time budgets per provider kind and per task (spec 11.6)."""

from __future__ import annotations

import pytest

from townrecord.ai.budgets import (
    BUDGET_CLOUD_MODEL,
    DEFAULT_BUDGETS,
    MEASURED_CALL_S,
    Budget,
    for_kind,
    for_task,
    parse_overrides,
)
from townrecord.ai.providers import (
    KIND_ANTHROPIC,
    KIND_CLAUDE_CLI,
    KIND_CODEX_CLI,
    KIND_LOCAL,
    KIND_OPENAI,
    KIND_OPENAI_COMPATIBLE,
    model_home,
)


class TestTheDefaults:
    def test_the_command_line_budget_is_the_spec_s(self) -> None:
        assert DEFAULT_BUDGETS[KIND_CLAUDE_CLI] == Budget(job_s=420.0, call_s=150.0)
        assert DEFAULT_BUDGETS[KIND_CODEX_CLI] == Budget(job_s=420.0, call_s=150.0)

    def test_the_api_budget_is_the_spec_s(self) -> None:
        for kind in (KIND_ANTHROPIC, KIND_OPENAI, KIND_OPENAI_COMPATIBLE):
            assert DEFAULT_BUDGETS[kind] == Budget(job_s=38.0, call_s=20.0)

    def test_the_local_budget_is_forty_minutes_and_ten(self) -> None:
        assert DEFAULT_BUDGETS[KIND_LOCAL] == Budget(job_s=2400.0, call_s=600.0)

    def test_every_kind_has_a_default(self) -> None:
        assert set(DEFAULT_BUDGETS) == {
            KIND_CLAUDE_CLI,
            KIND_CODEX_CLI,
            KIND_ANTHROPIC,
            KIND_OPENAI,
            KIND_OPENAI_COMPATIBLE,
            KIND_LOCAL,
        }

    def test_a_budget_is_a_positive_number_of_seconds(self) -> None:
        with pytest.raises(ValueError, match="positive number"):
            Budget(job_s=0.0, call_s=10.0)
        with pytest.raises(ValueError, match="positive number"):
            Budget(job_s=10.0, call_s=-1.0)

    def test_the_measured_call_is_the_spec_s_own_arithmetic(self) -> None:
        # 20 minutes across nine local-model calls.
        assert pytest.approx(133.33, abs=0.01) == MEASURED_CALL_S
        assert DEFAULT_BUDGETS[KIND_LOCAL].call_s > MEASURED_CALL_S


class TestChoosingABudget:
    def test_a_kind_with_no_override_gets_its_default(self) -> None:
        assert for_kind(KIND_LOCAL) == DEFAULT_BUDGETS[KIND_LOCAL]

    def test_a_kind_that_has_no_budget_at_all_is_refused(self) -> None:
        with pytest.raises(ValueError, match="has no time budget"):
            for_kind("a_kind_from_the_future")

    def test_an_override_changes_only_the_kind_it_names(self) -> None:
        overrides = {KIND_LOCAL: {"call_s": 60.0}}
        assert for_kind(KIND_LOCAL, overrides=overrides) == Budget(job_s=2400.0, call_s=60.0)
        assert for_kind(KIND_ANTHROPIC, overrides=overrides) == DEFAULT_BUDGETS[KIND_ANTHROPIC]

    def test_an_override_can_change_the_whole_job(self) -> None:
        overrides = {KIND_CLAUDE_CLI: {"job_s": 900.0, "call_s": 300.0}}
        assert for_task("summarize", KIND_CLAUDE_CLI, overrides=overrides) == Budget(
            job_s=900.0, call_s=300.0
        )


class TestWhereTheModelRuns:
    """The budget follows the model, not the row it was reached through (11.6)."""

    def test_a_cloud_model_on_a_local_row_gets_the_api_budget(self) -> None:
        """The named check: forty minutes is for a model on this machine."""
        cloud = model_home("kimi-k2.6:cloud", program="ollama")
        assert cloud.is_local is False
        assert for_task("summarize", KIND_LOCAL, home=cloud) == Budget(job_s=38.0, call_s=20.0)
        assert for_task("summarize", KIND_LOCAL, home=cloud) == BUDGET_CLOUD_MODEL

    def test_a_local_model_keeps_the_forty_minutes(self) -> None:
        here = model_home("qwen3:8b", program="ollama", entry={})
        assert for_task("summarize", KIND_LOCAL, home=here) == Budget(job_s=2400.0, call_s=600.0)

    def test_an_unknown_model_gets_the_api_budget_too(self) -> None:
        unknown = model_home("nobody-listed", program="ollama")
        assert unknown.known is False
        assert for_task("summarize", KIND_LOCAL, home=unknown) == BUDGET_CLOUD_MODEL

    def test_a_caller_that_has_not_asked_keeps_the_kind_s_budget(self) -> None:
        assert for_task("summarize", KIND_LOCAL) == DEFAULT_BUDGETS[KIND_LOCAL]
        assert for_kind(KIND_LOCAL) == DEFAULT_BUDGETS[KIND_LOCAL]

    def test_the_override_a_user_saved_still_wins(self) -> None:
        cloud = model_home("kimi-k2.6:cloud", program="ollama")
        overrides = {KIND_LOCAL: {"job_s": 100.0}}
        assert for_task("summarize", KIND_LOCAL, home=cloud, overrides=overrides) == Budget(
            job_s=100.0, call_s=20.0
        )


class TestStoredOverrides:
    def test_no_text_is_no_override_and_no_reason(self) -> None:
        assert parse_overrides("") == ({}, "")
        assert parse_overrides("   ") == ({}, "")

    def test_an_override_reads_back(self) -> None:
        overrides, reason = parse_overrides('{"local": {"job_s": 100, "call_s": 50}}')
        assert reason == ""
        assert overrides == {KIND_LOCAL: {"job_s": 100.0, "call_s": 50.0}}

    def test_text_that_is_not_json_is_reported_and_the_built_in_ones_used(self) -> None:
        overrides, reason = parse_overrides("not json", what="summarize budgets")
        assert overrides == {}
        assert "summarize budgets" in reason
        assert "built-in ones are used" in reason

    def test_json_that_is_not_an_object_is_reported(self) -> None:
        overrides, reason = parse_overrides('["local"]')
        assert overrides == {}
        assert "not a JSON object" in reason

    def test_a_value_that_is_not_a_number_is_left_out(self) -> None:
        overrides, reason = parse_overrides('{"local": {"call_s": "soon", "job_s": 10}}')
        assert reason == ""
        assert overrides == {KIND_LOCAL: {"job_s": 10.0}}

    def test_a_setting_that_is_not_a_budget_is_left_out(self) -> None:
        overrides, _reason = parse_overrides('{"local": {"colour": "blue", "call_s": 10}}')
        assert overrides == {KIND_LOCAL: {"call_s": 10.0}}

    def test_a_budget_a_user_edited_is_still_a_budget(self) -> None:
        overrides, reason = parse_overrides('{"local": {"call_s": 12}}')
        assert reason == ""
        assert for_kind(KIND_LOCAL, overrides=overrides).call_s == 12.0
