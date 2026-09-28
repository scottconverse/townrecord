"""The model layer: which model runs each task, and what it is allowed to cost.

Spec 11 in six pieces, and each piece is one module:

* :mod:`townrecord.ai.providers` is the registry of spec 11.1: every model the
  application can use is a row the user writes, never a release of this
  program.
* :mod:`townrecord.ai.discovery` finds the local model programs on the loopback
  address and lists what they hold. It never loads or unloads a model
  (spec 11.8), and it says where each model runs: a local program is not a
  local model, because Ollama serves some of its models from its own cloud.
* :mod:`townrecord.ai.ladder` is the tasks and their ladders of spec 11.4:
  either one provider the user picked, or "Automatic", an ordered ladder whose
  first reachable rung runs.
* :mod:`townrecord.ai.failover` is the rules of spec 11.5: which failures move
  down a rung, which one is final, and what a user-picked local model does.
* :mod:`townrecord.ai.budgets` is the time budgets of spec 11.6, and
  :mod:`townrecord.ai.commandline` is the exact argument lists of spec 11.3.
* :mod:`townrecord.ai.ledger` is the usage ledger and the estimate of
  spec 11.8, and :mod:`townrecord.ai.preflight` is the check every call goes
  through first.

:mod:`townrecord.ai.store` is where all of that meets the database
(migration 0016). Nothing in this package calls a model: a caller hands in the
HTTP client or the process runner, which is what lets the whole of it be tested
with fakes (PROJECT-BRIEF rule 9).
"""

from __future__ import annotations

from .budgets import (
    BUDGET_CLOUD_MODEL,
    DEFAULT_BUDGETS,
    Budget,
    for_kind,
    for_task,
    parse_overrides,
)
from .commandline import (
    CLAUDE_BASE_FLAGS,
    CLAUDE_NO_TOOLS,
    CLAUDE_PROGRAM,
    CLAUDE_SIGN_IN,
    CLAUDE_SIGN_IN_CHECK,
    CLAUDE_WEB_TOOLS,
    CODEX_BASE_FLAGS,
    CODEX_COLOR_FLAGS,
    CODEX_PROGRAM,
    CODEX_SIGN_IN,
    CODEX_SIGN_IN_CHECK,
    CODEX_WEB_FLAGS,
    argv_for,
    claude_argv,
    codex_argv,
    is_logout,
    refuse_logout,
    run_program,
    sign_in_argv,
    sign_in_sentence,
)
from .discovery import (
    LOCAL_ENDPOINTS,
    LOOPBACK_HOSTS,
    READ_ONLY_PATHS,
    DiscoveryResult,
    Found,
    LocalEndpoint,
    discover,
    probe,
)
from .failover import (
    FINAL_REASONS,
    MOVE_REASONS,
    REASON_CONTENT_REFUSAL,
    REASONS,
    Attempt,
    CallRecord,
    TaskRun,
    moves_on,
    run_task,
)
from .ladder import (
    AUTOMATIC_LABEL,
    DEFAULT_LADDERS,
    MODE_AUTOMATIC,
    MODE_PROVIDER,
    TASKS,
    Ladder,
    Resolution,
    Rung,
    default_ladder,
    default_rungs,
)
from .ledger import (
    Estimate,
    PlannedWork,
    calls_on,
    day_totals,
    estimate_calls,
    month_totals,
    record_call,
    total_for,
)
from .preflight import (
    Allowance,
    Checks,
    Preflight,
    live_checks,
    preflight,
)
from .providers import (
    CLOUD_NAME_MARKERS,
    COMMAND_KINDS,
    HTTP_KINDS,
    KEY_KINDS,
    KIND_ANTHROPIC,
    KIND_CLAUDE_CLI,
    KIND_CODEX_CLI,
    KIND_LOCAL,
    KIND_OPENAI,
    KIND_OPENAI_COMPATIBLE,
    KINDS,
    REMOTE_FIELDS,
    RUNS_HERE,
    RUNS_IN_CLOUD,
    ModelHome,
    Provider,
    ProviderRegistry,
    cloud_label,
    model_home,
    redact,
)
from .store import (
    Providers,
    TaskSetting,
    delete_provider,
    get_provider,
    registry,
    save_provider,
    save_task_setting,
    task_setting,
    task_settings,
)

#: The reading half of :mod:`townrecord.ai.store`, re-exported under a name
#: that is not ``providers``. The obvious name is taken by the module that
#: holds :class:`Provider`, and a package attribute called ``providers`` would
#: shadow it: ``from townrecord.ai import providers`` would then hand back a
#: function where a caller expects the registry's own module.
from .store import providers as configured_providers

__all__ = [
    "AUTOMATIC_LABEL",
    "Allowance",
    "Attempt",
    "BUDGET_CLOUD_MODEL",
    "Budget",
    "CLAUDE_BASE_FLAGS",
    "CLAUDE_NO_TOOLS",
    "CLAUDE_PROGRAM",
    "CLAUDE_SIGN_IN",
    "CLAUDE_SIGN_IN_CHECK",
    "CLAUDE_WEB_TOOLS",
    "CODEX_BASE_FLAGS",
    "CODEX_COLOR_FLAGS",
    "CODEX_PROGRAM",
    "CODEX_SIGN_IN",
    "CODEX_SIGN_IN_CHECK",
    "CLOUD_NAME_MARKERS",
    "CODEX_WEB_FLAGS",
    "COMMAND_KINDS",
    "CallRecord",
    "Checks",
    "DEFAULT_BUDGETS",
    "DEFAULT_LADDERS",
    "DiscoveryResult",
    "Estimate",
    "FINAL_REASONS",
    "Found",
    "HTTP_KINDS",
    "KEY_KINDS",
    "KINDS",
    "KIND_ANTHROPIC",
    "KIND_CLAUDE_CLI",
    "KIND_CODEX_CLI",
    "KIND_LOCAL",
    "KIND_OPENAI",
    "KIND_OPENAI_COMPATIBLE",
    "LOCAL_ENDPOINTS",
    "LOOPBACK_HOSTS",
    "Ladder",
    "LocalEndpoint",
    "MODE_AUTOMATIC",
    "MODE_PROVIDER",
    "MOVE_REASONS",
    "ModelHome",
    "PlannedWork",
    "Preflight",
    "Provider",
    "ProviderRegistry",
    "Providers",
    "READ_ONLY_PATHS",
    "REASONS",
    "REASON_CONTENT_REFUSAL",
    "REMOTE_FIELDS",
    "RUNS_HERE",
    "RUNS_IN_CLOUD",
    "Resolution",
    "Rung",
    "TASKS",
    "TaskRun",
    "TaskSetting",
    "argv_for",
    "calls_on",
    "claude_argv",
    "cloud_label",
    "codex_argv",
    "configured_providers",
    "day_totals",
    "default_ladder",
    "default_rungs",
    "delete_provider",
    "discover",
    "estimate_calls",
    "for_kind",
    "for_task",
    "get_provider",
    "is_logout",
    "live_checks",
    "model_home",
    "month_totals",
    "moves_on",
    "parse_overrides",
    "preflight",
    "probe",
    "record_call",
    "redact",
    "refuse_logout",
    "registry",
    "run_program",
    "run_task",
    "save_provider",
    "save_task_setting",
    "sign_in_argv",
    "sign_in_sentence",
    "task_setting",
    "task_settings",
    "total_for",
]
