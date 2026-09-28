"""The exact argument lists spec 11.3 gives, pinned as data."""

from __future__ import annotations

import pytest

from townrecord import proc
from townrecord.ai.commandline import (
    CLAUDE_BASE_FLAGS,
    CLAUDE_NO_TOOLS,
    CLAUDE_PROGRAM,
    CLAUDE_SIGN_IN,
    CLAUDE_SIGN_IN_CHECK,
    CLAUDE_SIGN_IN_TIMEOUT_S,
    CLAUDE_WEB_TOOLS,
    CODEX_BASE_FLAGS,
    CODEX_PROGRAM,
    CODEX_SIGN_IN,
    CODEX_SIGN_IN_CHECK,
    CODEX_SIGN_IN_TIMEOUT_S,
    CODEX_WEB_FLAGS,
    DEFAULT_CALL_TIMEOUT_S,
    LOGOUT_WORDS,
    REFUSED_SIGN_IN,
    argv_for,
    claude_argv,
    codex_argv,
    is_logout,
    refuse_logout,
    run_program,
    sign_in_argv,
    sign_in_sentence,
)

#: The claude argument list of spec 11.3, written out here exactly as that
#: section gives it, so a change to the constant has to be a change here too.
SPEC_CLAUDE_BASE = (
    "-p",
    "--restricted",
    "--strict-mcp-config",
    "--safe-mode",
    "--disable-slash-commands",
    "--permission-mode",
    "dontAsk",
    "--permission-prompts",
    "none",
    "--no-session-persistence",
    "--no-chrome",
    "--setting-sources",
    "",
)

#: The codex argument list of spec 11.3, written out the same way.
SPEC_CODEX_BASE = (
    "--ask-for-approval",
    "never",
    "--disable",
    "shell_tool,computer_use,browser_use,apps,plugins,multi_agent,hooks",
    "exec",
    "--ignore-user-config",
    "--skip-git-repo-check",
    "--sandbox",
    "read-only",
    "--ephemeral",
    "--json",
)


class TestTheArgumentListsMatchTheSpec:
    """The named check: the flags are the spec's, not this author's."""

    def test_the_claude_flags_are_the_spec_s(self) -> None:
        assert CLAUDE_BASE_FLAGS == SPEC_CLAUDE_BASE

    def test_the_codex_flags_are_the_spec_s(self) -> None:
        assert CODEX_BASE_FLAGS == SPEC_CODEX_BASE

    def test_a_claude_call_with_no_tools_is_the_base_flags_and_nothing_else(self) -> None:
        argv = claude_argv("read this page")
        assert argv == (CLAUDE_PROGRAM, *SPEC_CLAUDE_BASE, "--tools", "", "read this page")

    def test_a_codex_call_is_the_base_flags_with_no_colour(self) -> None:
        argv = codex_argv("read this page")
        assert argv == (
            CODEX_PROGRAM,
            *SPEC_CODEX_BASE,
            "--color",
            "never",
            "read this page",
        )

    def test_the_two_web_tools_are_the_only_tools_a_task_may_have(self) -> None:
        assert CLAUDE_NO_TOOLS == ("--tools", "")
        assert CLAUDE_WEB_TOOLS == ("--allowed-tools", "WebSearch,WebFetch")
        argv = claude_argv("look it up", web=True)
        assert "--allowed-tools" in argv
        assert argv[argv.index("--allowed-tools") + 1] == "WebSearch,WebFetch"
        assert "Bash" not in " ".join(argv)

    def test_web_search_is_added_to_codex_only_when_the_task_needs_it(self) -> None:
        assert CODEX_WEB_FLAGS == ("--enable", "standalone_web_search")
        assert "--enable" not in codex_argv("read this page")
        assert CODEX_WEB_FLAGS[-1] in codex_argv("look it up", web=True)

    def test_a_model_is_named_when_the_task_names_one(self) -> None:
        argv = claude_argv("hello", model="claude-opus-5-5")
        assert argv[argv.index("--model") + 1] == "claude-opus-5-5"
        assert "--model" not in claude_argv("hello")

    def test_the_prompt_is_the_last_argument(self) -> None:
        # The prompt is an argument of its own and never a word inside the
        # flags: a prompt joined into the command line is how a caller ends up
        # writing something the model reads as a flag.
        assert claude_argv("the prompt")[-1] == "the prompt"
        assert codex_argv("the prompt")[-1] == "the prompt"
        assert codex_argv("the prompt", web=True)[-1] == "the prompt"
        assert claude_argv("a b c")[-1] == "a b c"

    def test_a_task_never_gets_a_shell(self) -> None:
        # Spec 11.3's lesson: a model that can reach a shell tries to escape the
        # sandbox and writes about it. Neither program may be given one, and the
        # only mention of a shell tool is codex being told to disable it.
        for argv in (claude_argv("x"), claude_argv("x", web=True), codex_argv("x")):
            assert "-c" not in argv
            assert "--dangerously-skip-permissions" not in argv
            assert "--add-dir" not in argv
            assert "bash" not in " ".join(argv).lower()
            assert "shell" not in " ".join(argv).lower() or argv[1:5] == (
                "--ask-for-approval",
                "never",
                "--disable",
                "shell_tool,computer_use,browser_use,apps,plugins,multi_agent,hooks",
            )

    def test_the_arguments_are_a_list_and_never_one_string(self) -> None:
        for argv in (claude_argv("x"), codex_argv("x")):
            assert isinstance(argv, tuple)
            assert all(isinstance(part, str) for part in argv)


class TestSigningIn:
    def test_the_claude_sign_in_is_the_subscription_one(self) -> None:
        assert CLAUDE_SIGN_IN == ("claude", "auth", "login", "--claudeai")
        assert CLAUDE_SIGN_IN_TIMEOUT_S == 600.0

    def test_the_codex_sign_in_is_the_device_flow(self) -> None:
        assert CODEX_SIGN_IN == ("codex", "login", "--device-auth")
        assert CODEX_SIGN_IN_TIMEOUT_S == 900.0

    def test_the_plain_codex_login_is_refused_by_its_own_name(self) -> None:
        refused = [argv for argv, _reason in REFUSED_SIGN_IN]
        assert (CODEX_PROGRAM, "login") in refused
        assert "1455" in REFUSED_SIGN_IN[0][1]
        assert sign_in_argv(CODEX_PROGRAM) != (CODEX_PROGRAM, "login")

    def test_the_sign_in_sentence_names_the_command(self) -> None:
        assert "claude auth login --claudeai" in sign_in_sentence(CLAUDE_PROGRAM)
        assert "codex login --device-auth" in sign_in_sentence(CODEX_PROGRAM)

    def test_the_status_the_preflight_asks_is_not_a_sign_out(self) -> None:
        assert CLAUDE_SIGN_IN_CHECK[0] == CLAUDE_PROGRAM
        assert CODEX_SIGN_IN_CHECK[0] == CODEX_PROGRAM
        assert is_logout(CLAUDE_SIGN_IN_CHECK) is False
        assert is_logout(CODEX_SIGN_IN_CHECK) is False

    def test_argv_for_a_program_that_is_not_one_of_the_two_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not one of the two"):
            argv_for("bash", "x")
        with pytest.raises(ValueError, match="not one of the two"):
            sign_in_argv("python")


class TestNeverSigningTheUserOut:
    def test_a_logout_is_recognized_in_any_spelling(self) -> None:
        for word in LOGOUT_WORDS:
            assert is_logout(("claude", word)) is True

    def test_an_ordinary_call_is_not_a_logout(self) -> None:
        assert is_logout(claude_argv("x")) is False
        assert is_logout(codex_argv("x")) is False
        assert is_logout(CLAUDE_SIGN_IN) is False

    def test_a_logout_is_refused_with_a_plain_reason(self) -> None:
        with pytest.raises(ValueError, match="never does that"):
            refuse_logout(("claude", "logout"))

    def test_running_a_program_refuses_a_logout_before_it_runs_anything(self, runner) -> None:
        with pytest.raises(ValueError, match="never does that"):
            run_program(("codex", "logout"), runner=runner)
        assert runner.calls == []


class TestRunningAProgram:
    def test_a_call_goes_through_the_allow_listed_runner(self, runner) -> None:
        # The fake runner is what stands in for proc.run_allowlisted here, so a
        # test proves the call reaches it and never that a real model ran.
        argv = codex_argv("read the minutes")
        result = run_program(argv, runner=runner, timeout_s=150.0)
        assert runner.calls == [argv]
        assert runner.timeouts == [150.0]
        assert result.ok is True

    def test_the_timeout_a_caller_gives_is_the_one_used(self, runner) -> None:
        run_program(claude_argv("x"), runner=runner, timeout_s=20.0)
        assert runner.timeouts == [20.0]

    def test_a_caller_with_no_budget_gets_the_default(self, runner) -> None:
        run_program(claude_argv("x"), runner=runner)
        assert runner.timeouts == [DEFAULT_CALL_TIMEOUT_S]

    def test_a_failing_program_is_reported_by_its_return_code(self, runner) -> None:
        runner.returncode = 1
        runner.stderr = "not signed in"
        result = run_program(codex_argv("x"), runner=runner)
        assert result.ok is False
        assert result.last_stderr_line() == "not signed in"

    def test_the_runner_is_the_real_one_unless_a_caller_says_otherwise(self) -> None:
        import inspect

        assert inspect.signature(run_program).parameters["runner"].default is proc.run_allowlisted
