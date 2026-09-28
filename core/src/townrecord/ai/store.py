"""Reading and writing the AI settings, which are rows of migration 0016.

Nothing here decides anything. The registry, the ladders and the budgets are
:mod:`townrecord.ai.providers`, :mod:`townrecord.ai.ladder` and
:mod:`townrecord.ai.budgets`; this module is the one place those meet SQLite,
so there is one reading of every column rather than one per caller.

A value that cannot be read is reported and never guessed at. The JSON columns
(``models``, ``settings``, ``ladder``, ``budgets``) can hold text this version
cannot read, because a user can edit the database and a later version can write
a shape this one does not know. Each of those readings returns a reason in
plain words beside the value, and the value falls back to the built-in default,
which is the same habit as spec 16.3: report the problem, keep running.

A key passes through here and is never written anywhere else. No function in
this module puts a ``Provider`` into an exception message or a log line: the
rows are read into the frozen :class:`~townrecord.ai.providers.Provider`, whose
repr masks the key (spec 11.2).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .budgets import parse_overrides
from .ladder import (
    MODE_AUTOMATIC,
    MODE_PROVIDER,
    TASKS,
    Ladder,
    default_rungs,
)
from .providers import Provider, ProviderRegistry

#: The columns a provider row is read from, in one place so a SELECT and a
#: row reader cannot drift apart.
_PROVIDER_COLUMNS = "id, name, kind, base_url, api_key, models, settings"


def _json_value(text: Any, fallback: Any) -> tuple[Any, str]:
    """Read one JSON column. Return the value and why it could not be read."""
    raw = str(text or "").strip()
    if not raw:
        return fallback, ""
    try:
        return json.loads(raw), ""
    except ValueError:
        return fallback, f"it holds text that is not JSON ({raw[:40]!r})"


def _models_value(text: Any) -> tuple[tuple[str, ...], str]:
    """Read the ``models`` column as a tuple of names."""
    value, reason = _json_value(text, [])
    if reason:
        return (), f"The provider's model list cannot be read: {reason}."
    if not isinstance(value, (list, tuple)):
        return (), "The provider's model list is not a JSON array."
    return tuple(str(item).strip() for item in value if str(item).strip()), ""


def _settings_value(text: Any) -> tuple[dict[str, str], str]:
    """Read the ``settings`` column as a mapping of strings."""
    value, reason = _json_value(text, {})
    if reason:
        return {}, f"The provider's settings cannot be read: {reason}."
    if not isinstance(value, Mapping):
        return {}, "The provider's settings are not a JSON object."
    return {str(key): str(item) for key, item in value.items()}, ""


def _row_to_provider(row: sqlite3.Row) -> tuple[Provider, tuple[str, ...]]:
    """Read one ``ai_providers`` row. Return the provider and any problems."""
    problems: list[str] = []
    models, why = _models_value(row["models"])
    if why:
        problems.append(why)
    settings, why = _settings_value(row["settings"])
    if why:
        problems.append(why)
    provider = Provider(
        name=str(row["name"]),
        kind=str(row["kind"]),
        base_url=str(row["base_url"] or ""),
        api_key=str(row["api_key"] or ""),
        models=models,
        settings=settings,
        id=int(row["id"]) if row["id"] is not None else None,
    )
    return provider, tuple(problems)


@dataclass(frozen=True)
class Providers:
    """Every provider the user has configured, and what could not be read."""

    providers: tuple[Provider, ...] = ()
    problems: tuple[str, ...] = ()

    def registry(self) -> ProviderRegistry:
        """The registry those providers make (spec 11.1)."""
        return ProviderRegistry(self.providers)


def providers(conn: sqlite3.Connection) -> Providers:
    """Read every provider, in the order the user added them."""
    rows = conn.execute(f"SELECT {_PROVIDER_COLUMNS} FROM ai_providers ORDER BY id").fetchall()
    found: list[Provider] = []
    problems: list[str] = []
    for row in rows:
        try:
            provider, why = _row_to_provider(row)
        except ValueError as exc:
            problems.append(f"A provider row could not be read: {exc}")
            continue
        found.append(provider)
        problems.extend(why)
    return Providers(providers=tuple(found), problems=tuple(problems))


def registry(conn: sqlite3.Connection) -> ProviderRegistry:
    """The registry of every configured provider, ready for a ladder to resolve."""
    return providers(conn).registry()


def get_provider(conn: sqlite3.Connection, name: str) -> Provider | None:
    """Read one provider by the user's name for it, or None."""
    row = conn.execute(
        f"SELECT {_PROVIDER_COLUMNS} FROM ai_providers WHERE name = ?", (str(name),)
    ).fetchone()
    if row is None:
        return None
    return _row_to_provider(row)[0]


def get_provider_by_id(conn: sqlite3.Connection, provider_id: int) -> Provider | None:
    """Read one provider by its row id, or None."""
    row = conn.execute(
        f"SELECT {_PROVIDER_COLUMNS} FROM ai_providers WHERE id = ?", (int(provider_id),)
    ).fetchone()
    if row is None:
        return None
    return _row_to_provider(row)[0]


def save_provider(conn: sqlite3.Connection, provider: Provider) -> Provider:
    """Write one provider, replacing the row of the same name. Return it with its id.

    The name is the key, because the name is how a ladder and the ledger both
    refer to a provider: saving the same name twice is the user editing a
    provider, not adding a second one.
    """
    conn.execute(
        """
        INSERT INTO ai_providers (name, kind, base_url, api_key, models, settings)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (name) DO UPDATE SET
            kind = excluded.kind,
            base_url = excluded.base_url,
            api_key = excluded.api_key,
            models = excluded.models,
            settings = excluded.settings
        """,
        (
            provider.name,
            provider.kind,
            provider.base_url,
            provider.api_key,
            json.dumps(list(provider.models)),
            json.dumps(dict(provider.settings)),
        ),
    )
    saved = get_provider(conn, provider.name)
    if saved is None:  # pragma: no cover - the insert above just made it
        raise RuntimeError(f"{provider.name} was saved and could not be read back.")
    return saved


def delete_provider(conn: sqlite3.Connection, name: str) -> bool:
    """Remove one provider. Return True when a provider was removed.

    A task set to this provider is set back to Automatic in the same step, and
    its stored ladder is emptied, so the task falls back to its built-in ladder
    (spec 11.4). Two things would otherwise be wrong at once: the table's own
    CHECK refuses a ``provider`` row with no provider id, so the delete would
    fail, and a task left set to a provider that is gone would be a task set to
    nothing at all.
    """
    found = get_provider(conn, str(name))
    if found is None:
        return False
    if found.id is not None:
        conn.execute(
            """
            UPDATE ai_tasks SET mode = ?, provider_id = NULL, ladder = '[]'
            WHERE provider_id = ?
            """,
            (MODE_AUTOMATIC, found.id),
        )
    conn.execute("DELETE FROM ai_providers WHERE name = ?", (str(name),))
    return True


@dataclass(frozen=True)
class TaskSetting:
    """One task's model setting as the user left it (spec 11.4, 11.6)."""

    task: str
    ladder: Ladder
    budgets: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    #: True when the user has changed this task from its built-in setting.
    edited: bool = False
    #: What could not be read, in plain words. The built-in default was used.
    problems: tuple[str, ...] = ()

    @property
    def is_automatic(self) -> bool:
        """True when this task runs a ladder rather than one picked provider."""
        return self.ladder.is_automatic


def task_setting(
    conn: sqlite3.Connection, task: str, *, providers_set: Providers | None = None
) -> TaskSetting:
    """Read one task's setting, falling back to its built-in ladder.

    A task with no row has not been edited, and the built-in automatic ladder
    is what it runs. That is not a missing setting: the migration writes no
    rows, so an unedited task has exactly one source of truth, which is
    :data:`townrecord.ai.ladder.DEFAULT_LADDERS`.
    """
    if task not in TASKS:
        raise ValueError(f"{task!r} is not a task. The tasks are: {', '.join(TASKS)}.")
    row = conn.execute(
        "SELECT mode, provider_id, model, ladder, budgets FROM ai_tasks WHERE task = ?",
        (task,),
    ).fetchone()
    if row is None:
        return TaskSetting(task=task, ladder=Ladder.automatic(task, default_rungs(task)))

    problems: list[str] = []
    mode = str(row["mode"] or MODE_AUTOMATIC)
    picked_name = ""
    if mode == MODE_PROVIDER:
        provider_id = row["provider_id"]
        known = providers_set if providers_set is not None else providers(conn)
        chosen = next(
            (item for item in known.providers if item.id == provider_id),
            None,
        )
        if chosen is None:
            problems.append(
                f"The {task} task is set to a provider that is no longer configured, so its "
                "built-in ladder is used."
            )
            mode = MODE_AUTOMATIC
        else:
            picked_name = chosen.name

    ladder_data: Any = {"mode": mode, "provider": picked_name, "model": str(row["model"] or "")}
    stored_ladder, why = _json_value(row["ladder"], [])
    if why:
        problems.append(f"The {task} ladder cannot be read, so its built-in ladder is used: {why}.")
    elif isinstance(stored_ladder, (list, tuple)) and stored_ladder:
        ladder_data["ladder"] = list(stored_ladder)
    try:
        ladder = Ladder.from_json(task, ladder_data)
    except ValueError as exc:
        problems.append(f"The {task} ladder cannot be used, so its built-in one is: {exc}")
        ladder = Ladder.automatic(task, default_rungs(task))

    overrides, why = parse_overrides(str(row["budgets"] or "{}"), what=f"{task} budgets")
    if why:
        problems.append(why)
    return TaskSetting(
        task=task,
        ladder=ladder,
        budgets=overrides,
        edited=True,
        problems=tuple(problems),
    )


def save_task_setting(
    conn: sqlite3.Connection,
    task: str,
    *,
    ladder: Ladder | None = None,
    budgets: Mapping[str, Mapping[str, float]] | None = None,
) -> TaskSetting:
    """Write one task's setting, keeping the half the caller did not pass.

    A picked ladder stores the provider's row id, because that is what the
    table's foreign key holds and what survives the user renaming it. The
    ladder text is written as well for the picked case: it keeps the model name
    the user chose, which the mode column alone cannot carry.
    """
    if task not in TASKS:
        raise ValueError(f"{task!r} is not a task. The tasks are: {', '.join(TASKS)}.")
    current = task_setting(conn, task)
    chosen = ladder if ladder is not None else current.ladder
    amounts = dict(budgets) if budgets is not None else dict(current.budgets)

    provider_id: int | None = None
    if chosen.mode == MODE_PROVIDER:
        found = get_provider(conn, chosen.first().provider)
        if found is None or found.id is None:
            raise ValueError(
                f"The {task} task cannot be set to {chosen.first().provider!r}, which is not a "
                "configured provider."
            )
        provider_id = found.id

    conn.execute(
        """
        INSERT INTO ai_tasks (task, mode, provider_id, model, ladder, budgets, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        ON CONFLICT (task) DO UPDATE SET
            mode = excluded.mode,
            provider_id = excluded.provider_id,
            model = excluded.model,
            ladder = excluded.ladder,
            budgets = excluded.budgets,
            updated_at = excluded.updated_at
        """,
        (
            task,
            chosen.mode,
            provider_id,
            chosen.first().model,
            json.dumps([rung.as_json() for rung in chosen.rungs]),
            json.dumps(amounts),
        ),
    )
    return task_setting(conn, task)


def task_settings(
    conn: sqlite3.Connection, tasks: Sequence[str] = TASKS
) -> tuple[TaskSetting, ...]:
    """Read every task's setting, in the order spec 11.4 gives the tasks."""
    known = providers(conn)
    return tuple(task_setting(conn, task, providers_set=known) for task in tasks)
