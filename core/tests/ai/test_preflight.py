"""The check every AI call goes through first (spec 11.8, 11.2)."""

from __future__ import annotations

import pytest

from townrecord import proc
from townrecord.ai.commandline import (
    CLAUDE_SIGN_IN_CHECK,
    CODEX_SIGN_IN_CHECK,
    sign_in_argv,
)
from townrecord.ai.preflight import (
    CODE_DAILY_BUDGET,
    CODE_MONTHLY_BUDGET,
    CODE_NO_KEY,
    CODE_NO_PROGRAM,
    CODE_NO_PROVIDER,
    CODE_NOT_SIGNED_IN,
    CODE_OK,
    CODE_UNREACHABLE,
    SIGN_IN_CHECK_ARGV,
    Allowance,
    Checks,
    live_checks,
    preflight,
)
from townrecord.ai.providers import (
    KIND_ANTHROPIC,
    KIND_CLAUDE_CLI,
    KIND_LOCAL,
    Provider,
)

from .conftest import CLOUD_NAME, LOCAL_NAME, FakeLocalServer, FakeRunner, ollama_body


class TestAProviderThatIsReady:
    def test_a_local_provider_that_answers_is_ready(self, local_provider: Provider) -> None:
        answer = preflight(local_provider, checks=Checks(reachable=lambda provider: True))
        assert answer.ok is True
        assert answer.code == CODE_OK
        assert LOCAL_NAME in answer.sentence and "is ready" in answer.sentence

    def test_a_cloud_provider_with_a_key_is_ready(self, cloud_provider: Provider) -> None:
        answer = preflight(cloud_provider, task="summarize")
        assert answer.ok is True
        assert "for summarize" in answer.sentence

    def test_the_preflight_is_true_when_it_passes(self, cloud_provider: Provider) -> None:
        assert bool(preflight(cloud_provider)) is True


class TestAProviderThatIsNot:
    def test_no_provider_at_all_says_what_to_do(self) -> None:
        answer = preflight(None, task="ocr")
        assert answer.ok is False
        assert answer.code == CODE_NO_PROVIDER
        assert "No provider is configured for ocr" in answer.sentence
        assert "Settings" in answer.sentence

    def test_a_vendor_api_with_no_key_says_which_provider_and_what_to_do(self) -> None:
        provider = Provider(name="the paid API", kind=KIND_ANTHROPIC)
        answer = preflight(provider)
        assert answer.ok is False
        assert answer.code == CODE_NO_KEY
        assert CLOUD_NAME in answer.sentence
        assert "Add its key" in answer.sentence

    def test_a_command_program_that_is_not_installed_says_what_to_install(self) -> None:
        provider = Provider(name="the subscription", kind=KIND_CLAUDE_CLI)
        answer = preflight(provider, checks=Checks(which=lambda program: None))
        assert answer.ok is False
        assert answer.code == CODE_NO_PROGRAM
        assert "claude" in answer.sentence
        assert "Install claude" in answer.sentence

    def test_a_command_program_that_is_signed_out_names_the_sign_in_command(self) -> None:
        provider = Provider(name="the subscription", kind=KIND_CLAUDE_CLI)
        checks = Checks(
            which=lambda program: f"/usr/bin/{program}", signed_in=lambda provider: False
        )
        answer = preflight(provider, checks=checks)
        assert answer.ok is False
        assert answer.code == CODE_NOT_SIGNED_IN
        assert "not signed in" in answer.sentence
        assert "claude auth login --claudeai" in answer.sentence
        assert "Nothing was sent to the model" in answer.sentence

    def test_a_local_provider_that_is_stopped_says_to_start_it(
        self, local_provider: Provider
    ) -> None:
        answer = preflight(local_provider, checks=Checks(reachable=lambda provider: False))
        assert answer.ok is False
        assert answer.code == CODE_UNREACHABLE
        assert LOCAL_NAME in answer.sentence
        assert "not answering" in answer.sentence

    def test_a_caller_that_cannot_ask_leaves_the_question_alone(self) -> None:
        # No HTTP client and no runner means no answer, and guessing one would
        # be worse than not asking.
        provider = Provider(name="mine", kind=KIND_LOCAL)
        assert preflight(provider).ok is True


class TestTheBudgetThatIsLeft:
    def test_a_provider_over_its_daily_budget_is_stopped(self, cloud_provider: Provider) -> None:
        answer = preflight(cloud_provider, allowance=Allowance(calls_today=10, daily_limit=10))
        assert answer.ok is False
        assert answer.code == CODE_DAILY_BUDGET
        assert "whole daily budget of 10 calls" in answer.sentence

    def test_a_provider_over_its_monthly_budget_is_stopped(self, cloud_provider: Provider) -> None:
        answer = preflight(
            cloud_provider, allowance=Allowance(calls_this_month=99, monthly_limit=99)
        )
        assert answer.ok is False
        assert answer.code == CODE_MONTHLY_BUDGET
        assert "whole monthly budget of 99 calls" in answer.sentence

    def test_a_budget_with_room_left_lets_the_call_through(self, cloud_provider: Provider) -> None:
        answer = preflight(
            cloud_provider,
            allowance=Allowance(
                calls_today=9, daily_limit=10, calls_this_month=9, monthly_limit=100
            ),
        )
        assert answer.ok is True

    def test_a_limit_nobody_set_is_not_a_limit_of_zero(self, cloud_provider: Provider) -> None:
        answer = preflight(cloud_provider, allowance=Allowance(calls_today=1000))
        assert answer.ok is True


class TestTheLiveChecks:
    def test_a_local_provider_is_probed_with_a_list_request(self, local_provider: Provider) -> None:
        server = FakeLocalServer({11434: ollama_body("qwen3:8b")})
        checks = live_checks(server.client(), which=lambda program: None)
        assert checks.reachable is not None
        assert checks.reachable(local_provider) is True
        assert server.paths() == ["/api/tags"]

    def test_a_stopped_local_provider_is_not_reachable(self, local_provider: Provider) -> None:
        checks = live_checks(FakeLocalServer().client(), which=lambda program: None)
        assert checks.reachable is not None
        assert checks.reachable(local_provider) is False

    def test_a_vendor_api_is_not_probed_over_the_network(self, cloud_provider: Provider) -> None:
        # There is no free endpoint to ask, and spending a call to find out
        # whether a call would work is the opposite of a cost control.
        server = FakeLocalServer()
        checks = live_checks(server.client(), which=lambda program: None)
        assert checks.reachable is not None
        assert checks.reachable(cloud_provider) is True
        assert server.requests == []

    def test_a_command_provider_is_reachable_when_its_program_is_on_path(self) -> None:
        provider = Provider(name="c", kind=KIND_CLAUDE_CLI)
        checks = live_checks(
            FakeLocalServer().client(),
            runner=FakeRunner(),
            which=lambda program: "/usr/bin/" + program,
        )
        assert checks.reachable is not None
        assert checks.reachable(provider) is True

    def test_signing_in_is_asked_with_the_status_command_and_never_a_model_call(self) -> None:
        provider = Provider(name="c", kind=KIND_CLAUDE_CLI)
        runner = FakeRunner()
        checks = live_checks(
            FakeLocalServer().client(), runner=runner, which=lambda program: "/usr/bin/" + program
        )
        assert checks.signed_in is not None
        assert checks.signed_in(provider) is True
        assert runner.calls == [CLAUDE_SIGN_IN_CHECK]
        assert runner.calls[0] != sign_in_argv("claude"), "the check must not sign anyone in"

    def test_a_program_that_says_it_is_signed_out_is_not_signed_in(self) -> None:
        provider = Provider(name="x", kind=KIND_CLAUDE_CLI)
        runner = FakeRunner(returncode=1, stderr="not logged in")
        checks = live_checks(
            FakeLocalServer().client(), runner=runner, which=lambda program: "/usr/bin/" + program
        )
        assert checks.signed_in is not None
        assert checks.signed_in(provider) is False

    def test_a_status_command_that_cannot_run_is_not_signed_in(self) -> None:
        provider = Provider(name="x", kind=KIND_CLAUDE_CLI)

        def timed_out(argv, *, timeout_s=None, cwd=None):
            raise proc.ProcessTimedOut(tuple(argv), timeout_s or 0.0)

        checks = live_checks(
            FakeLocalServer().client(),
            runner=timed_out,
            which=lambda program: "/usr/bin/" + program,
        )
        assert checks.signed_in is not None
        assert checks.signed_in(provider) is False

    def test_a_status_command_that_cannot_start_is_not_signed_in(self) -> None:
        provider = Provider(name="x", kind=KIND_CLAUDE_CLI)

        def missing(argv, *, timeout_s=None, cwd=None):
            raise proc.ProcessFailed("no such program")

        checks = live_checks(
            FakeLocalServer().client(), runner=missing, which=lambda program: "/usr/bin/" + program
        )
        assert checks.signed_in is not None
        assert checks.signed_in(provider) is False

    def test_a_provider_that_is_not_a_program_is_signed_in_by_definition(self) -> None:
        checks = live_checks(FakeLocalServer().client(), runner=FakeRunner())
        assert checks.signed_in is not None
        assert checks.signed_in(Provider(name="m", kind=KIND_ANTHROPIC, api_key="k")) is True

    def test_a_program_with_no_status_command_is_not_taken_as_signed_in(self) -> None:
        checks = live_checks(FakeLocalServer().client(), runner=FakeRunner())
        assert checks.signed_in is not None
        assert checks.signed_in(Provider(name="b", kind=KIND_LOCAL)) is True  # not a program at all

    def test_each_program_has_a_status_command_of_its_own(self) -> None:
        assert SIGN_IN_CHECK_ARGV["claude"] == CLAUDE_SIGN_IN_CHECK
        assert SIGN_IN_CHECK_ARGV["codex"] == CODEX_SIGN_IN_CHECK
        with pytest.raises(KeyError):
            SIGN_IN_CHECK_ARGV["bash"]
