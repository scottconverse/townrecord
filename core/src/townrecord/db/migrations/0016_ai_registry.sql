-- The AI provider registry, the per-task model choice and the usage ledger
-- (spec 11.1, 11.2, 11.4, 11.6, 11.8).
--
-- Spec 11.1 asks for one place that defines every model the application can
-- use, and TownReporter's rule behind it is that a local model is "config, not
-- code". That place is `ai_providers`: adding Ollama, a LiteLLM gateway or an
-- Anthropic key is a row the user writes, never a release of this program.
--
-- `api_key` is where a key lives (spec 11.2). It is a column and not a second
-- file because the user's settings already live here: the API tokens of
-- migration 0002 and the per-source settings of migration 0013 are the same
-- idea. Nothing in `townrecord.ai` writes this column to a log, an error or a
-- checkpoint, and `townrecord.ai.providers.redact` is what takes a secret out
-- of text that is about to be printed.
--
-- `ai_tasks` holds one row per task the user has *changed*. A task with no row
-- is not missing: it means the user has not edited it, and the built-in
-- automatic ladder of `townrecord.ai.ladder.DEFAULT_LADDERS` is the honest
-- reading. Storing the defaults here as well would be two sources of truth for
-- one fact (PROJECT-BRIEF rule C), so this migration writes no rows.
--
-- `ladder` and `budgets` are JSON text for the same reason `sources.settings`
-- is (migration 0013): the order of a ladder and the minutes of a budget are
-- the user's settings, and a new knob must not be a migration. A value that
-- cannot be read is reported with its plain reason and the built-in default is
-- used, never a crash (spec 16.3).
--
-- `ai_usage` is the ledger of spec 11.8: one row per provider, task and local
-- day, with a count. The month is the first seven characters of `day`, so a
-- month total is a sum over the days that already exist and never a second row
-- that can disagree with them. The provider is held by name and not by a
-- foreign key on purpose: the name is how the user and the ledger both refer
-- to a provider, and a row of usage is history measured in the name that was
-- in use at the time.

CREATE TABLE ai_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The user's own name for this provider. A task ladder and the ledger
    -- both refer to a provider by this name.
    name TEXT NOT NULL UNIQUE CHECK (length(name) > 0),
    -- The kinds of spec 11.1: a local program on the loopback address, an
    -- OpenAI-compatible gateway, the Anthropic or OpenAI API, or one of the
    -- two subscription programs.
    kind TEXT NOT NULL CHECK (
        kind IN ('local', 'openai_compatible', 'anthropic', 'openai', 'claude_cli', 'codex_cli')
    ),
    -- Where it answers. Empty means the kind's own default address, which is
    -- what a local program and both vendor APIs have.
    base_url TEXT NOT NULL DEFAULT '',
    -- The key, or empty when there is none (a local program has no account).
    api_key TEXT NOT NULL DEFAULT '',
    -- The models the user has allowed on this provider, as a JSON array of
    -- names. Empty means the provider has not been asked to list them.
    models TEXT NOT NULL DEFAULT '[]',
    -- Anything else this provider needs, as a JSON object. It is text and not
    -- a set of columns so that a new knob is a setting and not a migration.
    settings TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE ai_tasks (
    -- One of the task names of spec 11.4: 'classify', 'discover', 'align',
    -- 'summarize', 'answer', 'fact-check', 'ocr', 'embed'.
    task TEXT PRIMARY KEY CHECK (length(task) > 0),
    -- 'automatic' runs the ladder below; 'provider' runs the one provider and
    -- model the user picked and nothing else.
    mode TEXT NOT NULL DEFAULT 'automatic' CHECK (mode IN ('automatic', 'provider')),
    provider_id INTEGER REFERENCES ai_providers (id) ON DELETE SET NULL,
    model TEXT NOT NULL DEFAULT '',
    -- The ordered ladder of spec 11.4, as a JSON array of
    -- {"provider": name, "kind": kind, "model": name} objects. It is JSON
    -- because the order is the setting: a row per rung would make the order a
    -- sort key the user cannot see.
    ladder TEXT NOT NULL DEFAULT '[]',
    -- Budget overrides for this task, keyed by provider kind:
    -- {"local": {"job_s": 2400, "call_s": 600}} (spec 11.6).
    budgets TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- A task the user picked a provider for names that provider.
    CHECK (mode <> 'provider' OR provider_id IS NOT NULL)
);

CREATE TABLE ai_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The provider name the call was made against, as it was at the time.
    provider TEXT NOT NULL CHECK (length(provider) > 0),
    task TEXT NOT NULL CHECK (length(task) > 0),
    -- The local day the calls happened on, as YYYY-MM-DD (spec 16.2 measures a
    -- day in the area's time zone, and a ledger that counted UTC days would
    -- move a late-evening run into tomorrow).
    day TEXT NOT NULL CHECK (length(day) = 10),
    calls INTEGER NOT NULL CHECK (calls > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (provider, task, day)
);

CREATE INDEX ai_usage_day_idx ON ai_usage (day, provider);
