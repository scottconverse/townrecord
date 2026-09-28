"""The one check every AI call goes through first.

Before a task starts, three questions have answers a user can act on: is the
provider the setting names reachable, is it signed in where that means
anything, and is there budget left for it. A failure here is a plain sentence
naming the provider and what to do about it, because a job that fails three
minutes in with "HTTP 401" tells the user nothing they can use.

What cannot be known is not guessed. An Anthropic or OpenAI key cannot be
checked without spending a call, so a preflight calls that provider reachable
and says so in the docstring rather than pretending. A command-line program is
reachable when its program is on PATH, and signed in when its own status
subcommand exits zero: a program that is not installed, or that says it is
signed out, both end in the same plain sentence, which names the command that
signs the user in (spec 11.2).

Every fact this module needs comes in through :class:`Checks`, so a test drives
all of it offline and the real probes are built in one place
(:func:`townrecord.ai.preflight.live_checks`).
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .. import proc
from .commandline import (
    CLAUDE_PROGRAM,
    CLAUDE_SIGN_IN_CHECK,
    CODEX_PROGRAM,
    CODEX_SIGN_IN_CHECK,
    sign_in_sentence,
)
from .providers import KIND_LOCAL, Provider

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation only
    import httpx

#: Why a preflight failed, or that it did not. A caller that wants to say
#: something of its own reads this rather than the sentence.
CODE_OK = "ok"
CODE_NO_PROVIDER = "no-provider"
CODE_NO_KEY = "no-key"
CODE_NO_PROGRAM = "no-program"
CODE_NOT_SIGNED_IN = "not-signed-in"
CODE_UNREACHABLE = "unreachable"
CODE_DAILY_BUDGET = "daily-budget"
CODE_MONTHLY_BUDGET = "monthly-budget"

#: How each program is asked whether it is signed in. Spec 11.3 warns that
#: these programs' flags change between versions and have to be checked during
#: build; this is one of them, kept separate so a change is one line.
SIGN_IN_CHECK_ARGV = {
    CLAUDE_PROGRAM: CLAUDE_SIGN_IN_CHECK,
    CODEX_PROGRAM: CODEX_SIGN_IN_CHECK,
}

#: How long a sign-in check may take. It is a question about local state.
SIGN_IN_CHECK_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class Checks:
    """What a preflight asks about the world.

    ``reachable`` and ``signed_in`` are None when the caller has no way to ask
    (no HTTP client, no process runner) and the question is then left alone
    rather than answered with a guess. ``which`` defaults to the real one,
    because finding a program on PATH costs nothing and never reaches out.
    """

    reachable: Callable[[Provider], bool] | None = None
    signed_in: Callable[[Provider], bool] | None = None
    which: Callable[[str], str | None] = shutil.which


@dataclass(frozen=True)
class Allowance:
    """How much of a provider's budget is left, as the ledger reports it.

    A limit of None means the user has set none, and the call is allowed: a
    limit nobody set is not a limit of zero.
    """

    calls_today: int = 0
    calls_this_month: int = 0
    daily_limit: int | None = None
    monthly_limit: int | None = None


@dataclass(frozen=True)
class Preflight:
    """Whether a task may start on a provider, and what to say when it may not."""

    ok: bool
    provider: str
    sentence: str
    code: str = CODE_OK

    def __bool__(self) -> bool:
        return self.ok


def preflight(
    provider: Provider | None,
    *,
    task: str = "",
    checks: Checks | None = None,
    allowance: Allowance | None = None,
) -> Preflight:
    """Check one provider before a task runs on it.

    The questions are asked in the order a user would ask them: is there a
    provider at all, does it have what it needs to be called, is it there, and
    is there budget left.
    """
    asked = checks or Checks()
    what = f" for {task}" if task else ""

    if provider is None:
        return Preflight(
            ok=False,
            provider="",
            code=CODE_NO_PROVIDER,
            sentence=(
                f"No provider is configured{what}. Pick one for this task in Settings, or add "
                "one the application can use."
            ),
        )

    name = provider.name
    if provider.requires_key and not provider.api_key.strip():
        return Preflight(
            ok=False,
            provider=name,
            code=CODE_NO_KEY,
            sentence=f"{name} has no API key. Add its key in Settings, then run this again.",
        )

    if provider.is_command:
        program = provider.program
        if asked.which(program) is None:
            return Preflight(
                ok=False,
                provider=name,
                code=CODE_NO_PROGRAM,
                sentence=(
                    f"The {program} program was not found on this computer, and {name} runs it. "
                    f"Install {program}, sign in, then run this again."
                ),
            )
        if asked.signed_in is not None and not asked.signed_in(provider):
            return Preflight(
                ok=False,
                provider=name,
                code=CODE_NOT_SIGNED_IN,
                sentence=(
                    f"{name} is not signed in. {sign_in_sentence(program)} Nothing was sent to "
                    "the model."
                ),
            )

    if asked.reachable is not None and not asked.reachable(provider):
        return Preflight(
            ok=False,
            provider=name,
            code=CODE_UNREACHABLE,
            sentence=(
                f"{provider.describe()} is not answering. Start it, or pick another provider "
                f"for this task, then run this again."
            ),
        )

    if allowance is not None:
        if allowance.daily_limit is not None and allowance.calls_today >= allowance.daily_limit:
            return Preflight(
                ok=False,
                provider=name,
                code=CODE_DAILY_BUDGET,
                sentence=(
                    f"{name} has used its whole daily budget of {allowance.daily_limit} calls. "
                    "It can run again tomorrow, or the budget can be raised in Settings."
                ),
            )
        if (
            allowance.monthly_limit is not None
            and allowance.calls_this_month >= allowance.monthly_limit
        ):
            return Preflight(
                ok=False,
                provider=name,
                code=CODE_MONTHLY_BUDGET,
                sentence=(
                    f"{name} has used its whole monthly budget of "
                    f"{allowance.monthly_limit} calls. Raise it in Settings to keep going."
                ),
            )

    return Preflight(
        ok=True,
        provider=name,
        sentence=f"{provider.describe()} is ready{what}.",
    )


def live_checks(
    client: httpx.Client,
    *,
    runner=proc.run_allowlisted,
    which: Callable[[str], str | None] = shutil.which,
) -> Checks:
    """The real probes: a list request for a local model, a status call for a program.

    A vendor API is reported reachable without a request, and that is a choice
    and not an oversight: there is no free endpoint to ask, and spending a call
    to find out whether a call would work is the opposite of a cost control
    (spec 11.8). The key check above is what covers those providers here, and
    the first real call is what finds out the rest.
    """
    from . import discovery

    def reachable(provider: Provider) -> bool:
        if provider.kind == KIND_LOCAL:
            answered, _reason = discovery.probe(client, provider.endpoint)
            return answered
        if provider.is_command:
            return which(provider.program) is not None
        return True

    def signed_in(provider: Provider) -> bool:
        if not provider.is_command:
            return True
        argv = SIGN_IN_CHECK_ARGV.get(provider.program)
        if argv is None:
            return False
        try:
            result = runner(argv, timeout_s=SIGN_IN_CHECK_TIMEOUT_S)
        except (proc.ProcessFailed, proc.ProcessTimedOut):
            return False
        return result.ok

    return Checks(reachable=reachable, signed_in=signed_in, which=which)
