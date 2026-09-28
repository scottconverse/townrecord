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
* :mod:`townrecord.ai.grounding` is spec 11.7: the evidence pack a model is
  given, the check on what it wrote, and the one repair round.

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
from .grounding import (
    CODE_EMPTY,
    CODE_NO_EVIDENCE,
    CODE_NO_HANDLE,
    CODE_NO_REPAIR,
    CODE_NOT_FOUND,
    CODE_NUMBER,
    CODE_QUOTE,
    CODE_REPAIR_CHANGED,
    CODE_REPAIR_FAILED,
    CODE_TALLY,
    CODE_TALLY_MISMATCH,
    CODE_UNKNOWN_HANDLE,
    HANDLE_PATTERN,
    NOT_FOUND,
    OUTCOME_KEPT,
    OUTCOME_REMOVED,
    OUTCOME_REPAIRED,
    PACK_PROMPT_LIMIT,
    Ask,
    Decision,
    EvidencePack,
    Facts,
    Grounded,
    LadderAsk,
    RepairAnswer,
    RepairRequest,
    Span,
    Verdict,
    changed_facts,
    check_answer,
    check_sentence,
    evidence_pack,
    facts,
    ground,
    handles_in,
    is_not_found,
    normalize,
    quotes_in,
    render_ms,
    split_sentences,
    tallies_in,
    without_handles,
)

#: The two names of :mod:`townrecord.ai.grounding` whose plain names another
#: module in this package already means something by: ``KINDS`` is the provider
#: kinds of spec 11.1 and ``CODE_OK`` is the preflight's code. They are
#: re-exported under names that say which list they belong to, so
#: ``from townrecord.ai import KINDS`` keeps meaning what it meant. The
#: module itself is imported below for a caller that wants the plain names.
from .grounding import CODE_OK as CODE_GROUNDED
from .grounding import KINDS as SPAN_KINDS
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
    "Allowance",
    "argv_for",
    "Ask",
    "Attempt",
    "AUTOMATIC_LABEL",
    "Budget",
    "BUDGET_CLOUD_MODEL",
    "CallRecord",
    "calls_on",
    "changed_facts",
    "check_answer",
    "check_sentence",
    "Checks",
    "claude_argv",
    "CLAUDE_BASE_FLAGS",
    "CLAUDE_NO_TOOLS",
    "CLAUDE_PROGRAM",
    "CLAUDE_SIGN_IN",
    "CLAUDE_SIGN_IN_CHECK",
    "CLAUDE_WEB_TOOLS",
    "cloud_label",
    "CLOUD_NAME_MARKERS",
    "CODE_EMPTY",
    "CODE_GROUNDED",
    "CODE_NO_EVIDENCE",
    "CODE_NO_HANDLE",
    "CODE_NO_REPAIR",
    "CODE_NOT_FOUND",
    "CODE_NUMBER",
    "CODE_QUOTE",
    "CODE_REPAIR_CHANGED",
    "CODE_REPAIR_FAILED",
    "CODE_TALLY",
    "CODE_TALLY_MISMATCH",
    "CODE_UNKNOWN_HANDLE",
    "codex_argv",
    "CODEX_BASE_FLAGS",
    "CODEX_COLOR_FLAGS",
    "CODEX_PROGRAM",
    "CODEX_SIGN_IN",
    "CODEX_SIGN_IN_CHECK",
    "CODEX_WEB_FLAGS",
    "COMMAND_KINDS",
    "configured_providers",
    "day_totals",
    "Decision",
    "DEFAULT_BUDGETS",
    "default_ladder",
    "DEFAULT_LADDERS",
    "default_rungs",
    "delete_provider",
    "discover",
    "DiscoveryResult",
    "Estimate",
    "estimate_calls",
    "evidence_pack",
    "EvidencePack",
    "facts",
    "Facts",
    "FINAL_REASONS",
    "for_kind",
    "for_task",
    "Found",
    "get_provider",
    "ground",
    "Grounded",
    "HANDLE_PATTERN",
    "handles_in",
    "HTTP_KINDS",
    "is_logout",
    "is_not_found",
    "KEY_KINDS",
    "KIND_ANTHROPIC",
    "KIND_CLAUDE_CLI",
    "KIND_CODEX_CLI",
    "KIND_LOCAL",
    "KIND_OPENAI",
    "KIND_OPENAI_COMPATIBLE",
    "KINDS",
    "Ladder",
    "LadderAsk",
    "live_checks",
    "LOCAL_ENDPOINTS",
    "LocalEndpoint",
    "LOOPBACK_HOSTS",
    "MODE_AUTOMATIC",
    "MODE_PROVIDER",
    "model_home",
    "ModelHome",
    "month_totals",
    "MOVE_REASONS",
    "moves_on",
    "normalize",
    "NOT_FOUND",
    "OUTCOME_KEPT",
    "OUTCOME_REMOVED",
    "OUTCOME_REPAIRED",
    "PACK_PROMPT_LIMIT",
    "parse_overrides",
    "PlannedWork",
    "preflight",
    "Preflight",
    "probe",
    "Provider",
    "ProviderRegistry",
    "Providers",
    "quotes_in",
    "READ_ONLY_PATHS",
    "REASON_CONTENT_REFUSAL",
    "REASONS",
    "record_call",
    "redact",
    "refuse_logout",
    "registry",
    "REMOTE_FIELDS",
    "render_ms",
    "RepairAnswer",
    "RepairRequest",
    "Resolution",
    "run_program",
    "run_task",
    "Rung",
    "RUNS_HERE",
    "RUNS_IN_CLOUD",
    "save_provider",
    "save_task_setting",
    "sign_in_argv",
    "sign_in_sentence",
    "Span",
    "SPAN_KINDS",
    "split_sentences",
    "tallies_in",
    "task_setting",
    "task_settings",
    "TaskRun",
    "TASKS",
    "TaskSetting",
    "total_for",
    "Verdict",
    "without_handles",
]
