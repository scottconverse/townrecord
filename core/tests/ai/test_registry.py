"""The provider registry and the two habits that keep a key out of a log."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import pytest

from townrecord.ai import store
from townrecord.ai.providers import (
    HTTP_KINDS,
    KEY_KINDS,
    KIND_ANTHROPIC,
    KIND_CLAUDE_CLI,
    KIND_CODEX_CLI,
    KIND_LOCAL,
    KIND_OPENAI,
    KIND_OPENAI_COMPATIBLE,
    KINDS,
    Provider,
    ProviderRegistry,
    redact,
)
from townrecord.ai.store import (
    delete_provider,
    get_provider,
    providers,
    registry,
    save_provider,
)

from .conftest import (
    LOCAL_NAME,
    TEST_KEY,
)


class TestTheKinds:
    def test_every_kind_of_spec_11_1_is_here(self) -> None:
        assert KINDS == (
            KIND_LOCAL,
            KIND_OPENAI_COMPATIBLE,
            KIND_ANTHROPIC,
            KIND_OPENAI,
            KIND_CLAUDE_CLI,
            KIND_CODEX_CLI,
        )

    def test_the_two_command_kinds_are_the_subscription_programs(self) -> None:
        assert Provider(name="c", kind=KIND_CLAUDE_CLI).program == "claude"
        assert Provider(name="x", kind=KIND_CODEX_CLI).program == "codex"
        assert Provider(name="l", kind=KIND_LOCAL).program == ""

    def test_only_the_two_vendor_apis_need_a_key(self) -> None:
        assert KEY_KINDS == (KIND_ANTHROPIC, KIND_OPENAI)
        assert Provider(name="g", kind=KIND_OPENAI_COMPATIBLE).requires_key is False
        assert Provider(name="l", kind=KIND_LOCAL).requires_key is False

    def test_every_http_kind_is_a_kind(self) -> None:
        assert set(HTTP_KINDS) <= set(KINDS)
        assert KIND_CLAUDE_CLI not in HTTP_KINDS

    def test_a_kind_that_is_not_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a provider kind"):
            Provider(name="mystery", kind="magic")

    def test_a_provider_needs_a_name(self) -> None:
        with pytest.raises(ValueError, match="needs a name"):
            Provider(name="  ", kind=KIND_LOCAL)


class TestTheRegistry:
    def test_a_provider_is_found_by_its_name(self, registry: ProviderRegistry) -> None:
        assert registry.get(LOCAL_NAME) is not None
        assert registry.get("nothing by that name") is None

    def test_a_rung_finds_a_provider_by_name_first_then_by_kind(
        self, registry: ProviderRegistry
    ) -> None:
        by_name = registry.find(name=LOCAL_NAME)
        assert by_name is not None and by_name.name == LOCAL_NAME
        by_kind = registry.find(kind=KIND_ANTHROPIC)
        assert by_kind is not None and by_kind.kind == KIND_ANTHROPIC
        assert registry.find(name="", kind="") is None

    def test_saving_the_same_name_twice_replaces_the_row(self, conn: sqlite3.Connection) -> None:
        first = save_provider(conn, Provider(name="mine", kind=KIND_LOCAL, base_url="u1"))
        second = save_provider(conn, Provider(name="mine", kind=KIND_LOCAL, base_url="u2"))
        assert first.id == second.id
        assert [item.base_url for item in providers(conn).providers] == ["u2"]

    def test_a_provider_round_trips_through_the_database(self, conn: sqlite3.Connection) -> None:
        saved = save_provider(
            conn,
            Provider(
                name="mine",
                kind=KIND_ANTHROPIC,
                api_key=TEST_KEY,
                models=("claude-sonnet-5",),
                settings={"note": "the one I pay for"},
            ),
        )
        assert saved.id is not None
        again = get_provider(conn, "mine")
        assert again is not None
        assert again.api_key == TEST_KEY
        assert again.models == ("claude-sonnet-5",)
        assert dict(again.settings) == {"note": "the one I pay for"}

    def test_deleting_a_provider_says_whether_it_was_there(self, conn: sqlite3.Connection) -> None:
        save_provider(conn, Provider(name="mine", kind=KIND_LOCAL))
        assert delete_provider(conn, "mine") is True
        assert delete_provider(conn, "mine") is False

    def test_the_registry_reads_every_row(self, conn: sqlite3.Connection) -> None:
        save_provider(conn, Provider(name="a", kind=KIND_LOCAL))
        save_provider(conn, Provider(name="b", kind=KIND_CODEX_CLI))
        assert registry(conn).names() == ("a", "b")

    def test_a_provider_row_this_version_cannot_read_is_reported_not_crashed(
        self, conn: sqlite3.Connection
    ) -> None:
        save_provider(conn, Provider(name="mine", kind=KIND_LOCAL))
        conn.execute("UPDATE ai_providers SET models = 'not json at all' WHERE name = 'mine'")
        reading = providers(conn)
        assert reading.providers[0].models == ()
        assert len(reading.problems) == 1
        assert "model list cannot be read" in reading.problems[0]

    def test_the_database_refuses_a_kind_this_version_does_not_know(
        self, conn: sqlite3.Connection
    ) -> None:
        # The rule is enforced where it is read and where it is written: a kind
        # this version cannot connect to is not a row it will hold.
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ai_providers (name, kind) "
                "VALUES ('the future', 'a_kind_from_the_future')"
            )
        assert providers(conn).providers == ()

    def test_a_row_this_version_cannot_read_is_refused_with_a_plain_sentence(self) -> None:
        # The reading path itself, without needing a row the database refuses.
        row = {
            "id": 1,
            "name": "from a later version",
            "kind": "a_kind_from_the_future",
            "base_url": "",
            "api_key": "",
            "models": "[]",
            "settings": "{}",
        }
        with pytest.raises(ValueError, match="is not a provider kind"):
            store._row_to_provider(row)

    def test_a_row_that_cannot_be_read_does_not_take_the_others_with_it(
        self, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save_provider(conn, Provider(name="mine", kind=KIND_LOCAL))
        save_provider(conn, Provider(name="yours", kind=KIND_LOCAL))
        reading = store._row_to_provider

        def refuses(row: Any) -> Any:
            if row["name"] == "mine":
                raise ValueError("a_kind_from_the_future is not a provider kind")
            return reading(row)

        monkeypatch.setattr(store, "_row_to_provider", refuses)
        found = providers(conn)
        assert [item.name for item in found.providers] == ["yours"]
        assert len(found.problems) == 1
        assert "A provider row could not be read" in found.problems[0]
        assert "a_kind_from_the_future" in found.problems[0]


class TestAKeyNeverLeaves:
    """PROJECT-BRIEF rule 9 and spec 11.2: the key is never logged or raised."""

    def test_a_provider_repr_shows_a_placeholder_not_the_key(self) -> None:
        provider = Provider(name="mine", kind=KIND_ANTHROPIC, api_key=TEST_KEY)
        assert TEST_KEY not in repr(provider)
        assert "[redacted]" in repr(provider)

    def test_a_registry_repr_holds_no_key(self) -> None:
        provider = Provider(name="mine", kind=KIND_ANTHROPIC, api_key=TEST_KEY)
        assert TEST_KEY not in repr(ProviderRegistry([provider]))

    def test_a_provider_with_no_key_prints_no_placeholder(self) -> None:
        assert "redacted" not in repr(Provider(name="mine", kind=KIND_LOCAL))

    def test_redact_takes_a_named_key_out_of_text(self) -> None:
        text = f"the gateway answered 401 for key {TEST_KEY} at the endpoint"
        cleaned = redact(text, (TEST_KEY,))
        assert TEST_KEY not in cleaned
        assert "[redacted]" in cleaned

    def test_redact_takes_out_a_key_nobody_named(self) -> None:
        text = "Authorization: Bearer sk-live-0123456789abcdef and a token=abcdefghijklmnop"
        cleaned = redact(text)
        assert "sk-live-0123456789abcdef" not in cleaned
        assert "abcdefghijklmnop" not in cleaned

    def test_a_provider_redacts_its_own_key(self) -> None:
        provider = Provider(name="mine", kind=KIND_ANTHROPIC, api_key=TEST_KEY)
        assert provider.redact(f"failed with {TEST_KEY}") == "failed with [redacted]"

    def test_a_short_value_is_not_mangled_out_of_ordinary_words(self) -> None:
        # A redactor that replaces "cat" with a placeholder in every sentence is
        # one a caller turns off, so the floor is deliberate.
        assert redact("the concatenation of it", ("cat",)) == "the concatenation of it"

    def test_a_key_never_reaches_a_log_record(self, caplog: pytest.LogCaptureFixture) -> None:
        provider = Provider(name="mine", kind=KIND_ANTHROPIC, api_key=TEST_KEY)
        logger = logging.getLogger("townrecord.ai.test")
        with caplog.at_level(logging.DEBUG, logger="townrecord.ai.test"):
            logger.debug("about to call %r", provider)
            logger.info("answer: %s", provider.redact(f"HTTP 401 key={TEST_KEY}"))
        assert TEST_KEY not in caplog.text
        assert "[redacted]" in caplog.text

    def test_an_error_raised_about_a_provider_holds_no_key(self) -> None:
        with pytest.raises(ValueError) as raised:
            Provider(name="mine", kind="magic", api_key=TEST_KEY)
        assert TEST_KEY not in str(raised.value)

    def test_a_failure_carrying_a_provider_s_words_can_be_cleaned_before_printing(self) -> None:
        # The path a real failure takes: a reply from a vendor names the key it
        # refused, and the sentence a job shows the user is built from the
        # cleaned text, so the key is gone before anything is written down.
        provider = Provider(name="mine", kind=KIND_ANTHROPIC, api_key=TEST_KEY)
        refused = f'HTTP 401 {{"error": {{"message": "invalid api_key: {TEST_KEY}"}}}}'
        assert TEST_KEY in refused
        detail = provider.redact(refused)
        assert TEST_KEY not in detail
        assert "[redacted]" in detail
        assert "invalid api_key" in detail

    def test_a_failure_in_a_row_is_cleaned_by_the_key_the_row_holds(
        self, conn: sqlite3.Connection
    ) -> None:
        save_provider(conn, Provider(name="mine", kind=KIND_ANTHROPIC, api_key=TEST_KEY))
        stored = get_provider(conn, "mine")
        assert stored is not None
        assert TEST_KEY not in stored.redact(f"the vendor said: {TEST_KEY}")
        assert TEST_KEY not in repr(stored)
        assert TEST_KEY not in repr(registry(conn))
