"""The exact argument lists the two subscription programs get (spec 11.2, 11.3).

The command-line models get no tools except the ones a task needs. Spec 11.3
records the TownReporter lesson behind that: an empty allowed-tools list still
showed the model a set of refused tools, it tried to use a shell, was refused,
and wrote about escaping the sandbox into the leads. The answer is to run with
no tools and let the application do all fetching.

The flags are data here, and a test pins them, because spec 11.3 says plainly
that they change and have to be checked against each program's current version
during build. A flag that moves is then a one-line change with a failing test
in front of it, rather than a string assembled somewhere in a job.

Two rules that are not flags live here too:

* this application never runs a logout command (spec 11.2), because that would
  sign the user out of their own tools. :func:`refuse_logout` is the check
  every command this module runs goes through;
* the ChatGPT sign-in is the device flow. Plain ``codex login`` binds the fixed
  port 1455, so it is refused rather than used (spec 11.2).
"""

from __future__ import annotations

from collections.abc import Sequence

from .. import proc

#: The two programs (spec 11.1).
CLAUDE_PROGRAM = "claude"
CODEX_PROGRAM = "codex"

#: The flags claude runs with, exactly as spec 11.3 gives them. The empty
#: string after ``--setting-sources`` is the setting, not a missing value: it
#: is how the program is told to read no setting source at all.
CLAUDE_BASE_FLAGS: tuple[str, ...] = (
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

#: No tools at all, which is what most tasks get (spec 11.3).
CLAUDE_NO_TOOLS: tuple[str, ...] = ("--tools", "")

#: The only two tools a task may have, and only when it needs the web.
CLAUDE_WEB_TOOLS: tuple[str, ...] = ("--allowed-tools", "WebSearch,WebFetch")

#: The flags codex runs with, exactly as spec 11.3 gives them.
CODEX_BASE_FLAGS: tuple[str, ...] = (
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

#: codex writes no colour, because its output is read by this program.
CODEX_COLOR_FLAGS: tuple[str, ...] = ("--color", "never")

#: The one extra the task may ask for: web search, and only when it needs it.
CODEX_WEB_FLAGS: tuple[str, ...] = ("--enable", "standalone_web_search")

#: The sign-in commands of spec 11.2. Claude prints a URL and listens on a
#: random loopback port, so it gets ten minutes. ChatGPT's device flow gives a
#: URL and a code that lasts fifteen.
CLAUDE_SIGN_IN: tuple[str, ...] = (CLAUDE_PROGRAM, "auth", "login", "--claudeai")
CLAUDE_SIGN_IN_TIMEOUT_S = 600.0
CODEX_SIGN_IN: tuple[str, ...] = (CODEX_PROGRAM, "login", "--device-auth")
CODEX_SIGN_IN_TIMEOUT_S = 900.0

#: The sign-in commands a caller must not use, with the reason it must not.
REFUSED_SIGN_IN: tuple[tuple[tuple[str, ...], str], ...] = (
    ((CODEX_PROGRAM, "login"), "plain `codex login` binds the fixed port 1455 (spec 11.2)"),
)

#: How each program is asked whether it is signed in, for the preflight.
#:
#: NOT PINNED BY THE SPEC, and that is worth saying out loud rather than
#: dressing up. Spec 11.2 gives the *sign-in* commands and spec 11.3 gives the
#: call flags; neither gives a status command, and the spec's own warning
#: applies here most of all: these programs change their subcommands between
#: versions, so both of these have to be run against the installed version
#: during build (they are recorded in the P1 report as unverified). What is
#: checked by a test is that the preflight uses these and only these, and that
#: neither is a logout.
CLAUDE_SIGN_IN_CHECK: tuple[str, ...] = (CLAUDE_PROGRAM, "auth", "status")
CODEX_SIGN_IN_CHECK: tuple[str, ...] = (CODEX_PROGRAM, "login", "status")

#: The subcommands that end a session. This application never runs one
#: (spec 11.2): signing the user out of their own tools is not its business.
LOGOUT_WORDS: frozenset[str] = frozenset(
    {"logout", "log-out", "signout", "sign-out", "sign_off", "signoff", "sign-off"}
)

#: How long a model call may take when the caller names no budget. The
#: per-kind budgets of spec 11.6 are :mod:`townrecord.ai.budgets`; this is the
#: backstop for a caller that has none.
DEFAULT_CALL_TIMEOUT_S = 150.0


def is_logout(argv: Sequence[str]) -> bool:
    """True when this argument list would sign the user out of their tools."""
    return any(str(part).strip().lower() in LOGOUT_WORDS for part in argv)


def refuse_logout(argv: Sequence[str]) -> None:
    """Raise when this argument list is a logout command (spec 11.2)."""
    if is_logout(argv):
        joined = " ".join(str(part) for part in argv)
        raise ValueError(
            f"{joined} would sign the user out of their own tools, and TownRecord never does that."
        )


def claude_argv(prompt: str, *, model: str = "", web: bool = False) -> tuple[str, ...]:
    """The argument list one claude call runs with (spec 11.3).

    The exact flags are :data:`CLAUDE_BASE_FLAGS`, then the model when the task
    names one, then the tools: none at all, or the two web tools when the task
    needs them. The prompt is the last argument.
    """
    argv = [CLAUDE_PROGRAM, *CLAUDE_BASE_FLAGS]
    if model:
        argv += ["--model", model]
    argv += list(CLAUDE_WEB_TOOLS if web else CLAUDE_NO_TOOLS)
    argv.append(prompt)
    refuse_logout(argv)
    return tuple(argv)


def codex_argv(prompt: str, *, model: str = "", web: bool = False) -> tuple[str, ...]:
    """The argument list one codex call runs with (spec 11.3).

    The exact flags are :data:`CODEX_BASE_FLAGS`, then the model when the task
    names one, then no colour, then web search when the task needs it. The
    prompt is the last argument.
    """
    argv = [CODEX_PROGRAM, *CODEX_BASE_FLAGS]
    if model:
        argv += ["--model", model]
    argv += list(CODEX_COLOR_FLAGS)
    if web:
        argv += list(CODEX_WEB_FLAGS)
    argv.append(prompt)
    refuse_logout(argv)
    return tuple(argv)


def argv_for(
    provider_program: str, prompt: str, *, model: str = "", web: bool = False
) -> tuple[str, ...]:
    """The argument list for one of the two programs, by its program name."""
    if provider_program == CLAUDE_PROGRAM:
        return claude_argv(prompt, model=model, web=web)
    if provider_program == CODEX_PROGRAM:
        return codex_argv(prompt, model=model, web=web)
    raise ValueError(f"{provider_program!r} is not one of the two command-line models.")


def sign_in_argv(program: str) -> tuple[str, ...]:
    """The command that signs one program in (spec 11.2)."""
    if program == CLAUDE_PROGRAM:
        return CLAUDE_SIGN_IN
    if program == CODEX_PROGRAM:
        return CODEX_SIGN_IN
    raise ValueError(f"{program!r} is not one of the two command-line models.")


def sign_in_sentence(program: str) -> str:
    """A plain sentence telling the user how to sign one program in."""
    return f"Run `{' '.join(sign_in_argv(program))}` and follow the prompt."


def run_program(
    argv: Sequence[str],
    *,
    timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
    runner=proc.run_allowlisted,
    cwd: str | None = None,
) -> proc.ProcessResult:
    """Run one of the two programs, with no shell and no logout ever.

    It goes through :func:`townrecord.proc.run_allowlisted`, so the child gets
    the allow-listed environment of PROJECT-BRIEF rule E and never a shell.
    ``runner`` exists so a test hands in a fake and no real model is called
    (rule 9).
    """
    refuse_logout(argv)
    return runner(argv, timeout_s=timeout_s, cwd=cwd)
