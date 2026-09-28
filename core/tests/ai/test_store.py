"""The AI settings as rows: providers, one task's setting, what cannot be read."""

from __future__ import annotations

import sqlite3

import pytest

from townrecord.ai import store
from townrecord.ai.ladder import (
    DEFAULT_LADDERS,
    MODE_PROVIDER,
    TASKS,
    Ladder,
    Rung,
)
from townrecord.ai.providers import KIND_ANTHROPIC, KIND_LOCAL, Provider

from .conftest import CLOUD_NAME, LOCAL_NAME, TEST_KEY


@pytest.fixture
def saved_local(conn: sqlite3.Connection, local_provider: Provider) -> Provider:
    """The local provider, written to the database."""
    return store.save_provider(conn, local_provider)


class TestProvidersInTheDatabase:
    def test_a_provider_reads_back_as_it_was_written(
        self, conn: sqlite3.Connection, cloud_provider: Provider
    ) -> None:
        written = store.save_provider(conn, cloud_provider)
        assert written.id is not None
        read_back = store.get_provider(conn, CLOUD_NAME)
        assert read_back == written
        assert read_back is not None
        assert read_back.kind == KIND_ANTHROPIC
        assert read_back.api_key == TEST_KEY

    def test_a_name_is_the_key_and_saving_it_twice_edits_it(
        self, conn: sqlite3.Connection, local_provider: Provider
    ) -> None:
        first = store.save_provider(conn, local_provider)
        again = store.save_provider(
            conn, Provider(name=LOCAL_NAME, kind=KIND_LOCAL, models=("qwen3:8b", "llama3.2:3b"))
        )
        assert again.id == first.id
        assert store.providers(conn).providers == (again,)

    def test_the_models_and_settings_of_a_row_come_back(self, conn: sqlite3.Connection) -> None:
        store.save_provider(
            conn,
            Provider(
                name=LOCAL_NAME,
                kind=KIND_LOCAL,
                base_url="http://127.0.0.1:11434",
                models=("qwen3:8b", "llama3.2:3b"),
                settings={"keep_alive": "5m"},
            ),
        )
        read_back = store.get_provider(conn, LOCAL_NAME)
        assert read_back is not None
        assert read_back.models == ("qwen3:8b", "llama3.2:3b")
        assert read_back.settings == {"keep_alive": "5m"}

    def test_providers_are_read_in_the_order_they_were_added(
        self, conn: sqlite3.Connection, local_provider: Provider, cloud_provider: Provider
    ) -> None:
        store.save_provider(conn, cloud_provider)
        store.save_provider(conn, local_provider)
        assert [item.name for item in store.providers(conn).providers] == [
            CLOUD_NAME,
            LOCAL_NAME,
        ]

    def test_the_registry_of_the_rows_finds_a_provider(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        found = store.registry(conn).find(name=LOCAL_NAME)
        assert found == saved_local

    def test_a_provider_that_was_never_configured_is_not_found(
        self, conn: sqlite3.Connection
    ) -> None:
        assert store.get_provider(conn, "nobody") is None
        assert store.providers(conn).providers == ()
        assert store.providers(conn).problems == ()

    def test_a_provider_is_removed_by_its_name(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        assert store.delete_provider(conn, LOCAL_NAME) is True
        assert store.get_provider(conn, LOCAL_NAME) is None
        assert store.delete_provider(conn, LOCAL_NAME) is False


class TestAValueThatCannotBeRead:
    def test_an_unreadable_model_list_is_reported_and_the_row_kept(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        conn.execute("UPDATE ai_providers SET models = ? WHERE name = ?", ("not json", LOCAL_NAME))
        found = store.providers(conn)
        assert [item.name for item in found.providers] == [LOCAL_NAME]
        assert found.providers[0].models == ()
        assert any("model list cannot be read" in problem for problem in found.problems)

    def test_settings_that_are_not_an_object_are_reported(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        conn.execute("UPDATE ai_providers SET settings = ? WHERE name = ?", ('["a"]', LOCAL_NAME))
        found = store.providers(conn)
        assert found.providers[0].settings == {}
        assert any("settings are not a JSON object" in problem for problem in found.problems)

    def test_a_model_list_that_is_not_an_array_is_reported(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        conn.execute("UPDATE ai_providers SET models = ? WHERE name = ?", ('{"a": 1}', LOCAL_NAME))
        found = store.providers(conn)
        assert found.providers[0].models == ()
        assert any("not a JSON array" in problem for problem in found.problems)


class TestOneTaskSetting:
    def test_a_task_nobody_edited_runs_its_built_in_ladder(self, conn: sqlite3.Connection) -> None:
        setting = store.task_setting(conn, "summarize")
        assert setting.is_automatic is True
        assert setting.edited is False
        assert setting.ladder.rungs == DEFAULT_LADDERS["summarize"]
        assert setting.problems == ()
        assert setting.budgets == {}

    def test_a_task_that_is_not_one_of_the_eight_is_refused(self, conn: sqlite3.Connection) -> None:
        with pytest.raises(ValueError, match="is not a task"):
            store.task_setting(conn, "transcribe")

    def test_every_task_is_read_in_the_spec_s_order(self, conn: sqlite3.Connection) -> None:
        settings = store.task_settings(conn)
        assert [setting.task for setting in settings] == list(TASKS)
        assert all(not setting.edited for setting in settings)

    def test_a_picked_provider_is_stored_by_its_row_id(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        store.save_task_setting(conn, "summarize", ladder=Ladder.picked("summarize", LOCAL_NAME))
        row = conn.execute("SELECT mode, provider_id FROM ai_tasks WHERE task = ?", ("summarize",))
        stored = row.fetchone()
        assert stored["mode"] == MODE_PROVIDER
        assert stored["provider_id"] == saved_local.id
        setting = store.task_setting(conn, "summarize")
        assert setting.is_automatic is False
        assert setting.edited is True
        assert setting.ladder.first().provider == LOCAL_NAME

    def test_a_picked_task_names_the_model_the_user_chose(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        store.save_task_setting(
            conn, "ocr", ladder=Ladder.picked("ocr", LOCAL_NAME, model="qwen3:8b")
        )
        setting = store.task_setting(conn, "ocr")
        assert setting.ladder.first().model == "qwen3:8b"
        assert setting.ladder.fails_closed(store.registry(conn)) is True

    def test_a_task_picked_to_a_provider_that_is_not_configured_is_refused(
        self, conn: sqlite3.Connection
    ) -> None:
        with pytest.raises(ValueError, match="not a configured provider"):
            store.save_task_setting(
                conn, "summarize", ladder=Ladder.picked("summarize", "a name nobody added")
            )

    def test_removing_the_provider_sets_the_task_back_to_automatic(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        store.save_task_setting(conn, "summarize", ladder=Ladder.picked("summarize", LOCAL_NAME))
        assert store.delete_provider(conn, LOCAL_NAME) is True
        setting = store.task_setting(conn, "summarize")
        assert setting.is_automatic is True
        assert setting.ladder.rungs == DEFAULT_LADDERS["summarize"]

    def test_a_task_left_pointing_at_a_provider_that_is_gone_is_reported(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        # A hand-edited database can hold this, which is why the reading exists
        # even though delete_provider above keeps it from happening by itself.
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO ai_tasks (task, mode, provider_id, model) VALUES (?, ?, ?, ?)",
            ("summarize", MODE_PROVIDER, 4242, ""),
        )
        setting = store.task_setting(conn, "summarize")
        assert setting.is_automatic is True
        assert setting.ladder.rungs == DEFAULT_LADDERS["summarize"]
        assert any("no longer configured" in problem for problem in setting.problems)

    def test_a_ladder_that_cannot_be_read_falls_back_and_says_so(
        self, conn: sqlite3.Connection
    ) -> None:
        conn.execute(
            "INSERT INTO ai_tasks (task, mode, ladder) VALUES (?, 'automatic', ?)",
            ("answer", "not json"),
        )
        setting = store.task_setting(conn, "answer")
        assert setting.ladder.rungs == DEFAULT_LADDERS["answer"]
        assert setting.edited is True
        assert any("built-in ladder is used" in problem for problem in setting.problems)

    def test_a_rung_of_an_unknown_kind_falls_back_and_says_so(
        self, conn: sqlite3.Connection
    ) -> None:
        conn.execute(
            "INSERT INTO ai_tasks (task, mode, ladder) VALUES (?, 'automatic', ?)",
            ("answer", '[{"provider": "", "kind": "a_kind_from_the_future"}]'),
        )
        setting = store.task_setting(conn, "answer")
        assert setting.ladder.rungs == DEFAULT_LADDERS["answer"]
        assert any("built-in one is" in problem for problem in setting.problems)

    def test_a_stored_ladder_reads_back_in_its_order(self, conn: sqlite3.Connection) -> None:
        ladder = Ladder.automatic(
            "summarize", (Rung(kind=KIND_LOCAL), Rung(provider="a gateway I named"))
        )
        store.save_task_setting(conn, "summarize", ladder=ladder)
        read_back = store.task_setting(conn, "summarize").ladder
        assert read_back.rungs == ladder.rungs
        assert read_back.is_automatic is True

    def test_saving_one_half_keeps_the_other(self, conn: sqlite3.Connection) -> None:
        store.save_task_setting(conn, "summarize", budgets={KIND_LOCAL: {"call_s": 60.0}})
        store.save_task_setting(
            conn,
            "summarize",
            ladder=Ladder.automatic("summarize", (Rung(kind=KIND_LOCAL),)),
        )
        setting = store.task_setting(conn, "summarize")
        assert setting.budgets == {KIND_LOCAL: {"call_s": 60.0}}

    def test_budgets_a_user_edited_are_read_back_as_budgets(self, conn: sqlite3.Connection) -> None:
        store.save_task_setting(
            conn, "ocr", budgets={KIND_LOCAL: {"job_s": 1200.0, "call_s": 300.0}}
        )
        setting = store.task_setting(conn, "ocr")
        assert setting.budgets == {KIND_LOCAL: {"job_s": 1200.0, "call_s": 300.0}}
        assert setting.problems == ()

    def test_budgets_that_cannot_be_read_are_reported_and_the_built_in_ones_used(
        self, conn: sqlite3.Connection
    ) -> None:
        conn.execute(
            "INSERT INTO ai_tasks (task, mode, budgets) VALUES (?, 'automatic', ?)",
            ("ocr", "twenty minutes"),
        )
        setting = store.task_setting(conn, "ocr")
        assert setting.budgets == {}
        assert any("built-in ones are used" in problem for problem in setting.problems)

    def test_one_task_s_setting_does_not_touch_another_s(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        store.save_task_setting(conn, "summarize", ladder=Ladder.picked("summarize", LOCAL_NAME))
        assert store.task_setting(conn, "ocr").is_automatic is True

    def test_the_task_setting_survives_a_second_reading(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        store.save_task_setting(conn, "summarize", ladder=Ladder.picked("summarize", LOCAL_NAME))
        store.save_task_setting(conn, "summarize", ladder=Ladder.picked("summarize", LOCAL_NAME))
        rows = conn.execute("SELECT task FROM ai_tasks").fetchall()
        assert [row["task"] for row in rows] == ["summarize"]

    def test_a_provider_set_is_reused_instead_of_read_again(
        self, conn: sqlite3.Connection, saved_local: Provider
    ) -> None:
        store.save_task_setting(conn, "summarize", ladder=Ladder.picked("summarize", LOCAL_NAME))
        known = store.providers(conn)
        setting = store.task_setting(conn, "summarize", providers_set=known)
        assert setting.ladder.first().provider == LOCAL_NAME
        assert setting.problems == ()
