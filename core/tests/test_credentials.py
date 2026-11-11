"""The API key loader of spec 17: the OS credential store, and an environment variable.

Every key here is a fake. No test reads the machine's real credential: the
store is replaced by a fake, except the one Windows round trip, which writes
and deletes its own fake key under its own target.
"""

from __future__ import annotations

import ctypes
import sys
import uuid
from typing import Any

import pytest

from townrecord import credentials
from townrecord.credentials import (
    ERROR_NOT_FOUND,
    YOUTUBE_API_KEY_ENV,
    YOUTUBE_API_KEY_TARGET,
    read_windows_credential,
    youtube_api_key,
)

FAKE_KEY = "fake-key-for-tests-0000"


class FakeAdvapi:
    """A CredReadW that always fails with one Windows error code."""

    def __init__(self, error: int) -> None:
        self.error = error
        self.targets: list[str] = []
        self.freed = 0

    def CredReadW(self, target: str, cred_type: int, flags: int, out: Any) -> int:  # noqa: N802
        self.targets.append(target)
        return 0

    def CredFree(self, pointer: Any) -> None:  # noqa: N802
        self.freed += 1

    def last_error(self) -> int:
        return self.error


def test_a_credential_that_is_not_there_reads_as_none() -> None:
    """ERROR_NOT_FOUND (1168) is the normal answer on a machine with no key."""
    api = FakeAdvapi(ERROR_NOT_FOUND)
    assert read_windows_credential("TownRecord/not-there", api=api) is None
    assert api.targets == ["TownRecord/not-there"]
    assert api.freed == 0


def test_any_other_store_error_reads_as_none_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A store that refuses for another reason gives no key, and says why in the log."""
    api = FakeAdvapi(5)  # ERROR_ACCESS_DENIED
    assert read_windows_credential("TownRecord/refused", api=api) is None
    assert "5" in caplog.text and "TownRecord/refused" in caplog.text


def test_the_environment_variable_overrides_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec 17: "An environment variable overrides a saved key." """
    monkeypatch.setenv(YOUTUBE_API_KEY_ENV, f"  {FAKE_KEY}\n")
    monkeypatch.setattr(credentials, "read_credential", lambda target: "fake-stored-key")
    assert youtube_api_key() == FAKE_KEY


def test_the_store_is_read_when_the_environment_has_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []
    monkeypatch.setenv(YOUTUBE_API_KEY_ENV, "   ")
    monkeypatch.setattr(
        credentials, "read_credential", lambda target: asked.append(target) or FAKE_KEY
    )
    assert youtube_api_key() == FAKE_KEY
    assert asked == [YOUTUBE_API_KEY_TARGET]


def test_no_key_anywhere_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(YOUTUBE_API_KEY_ENV, raising=False)
    monkeypatch.setattr(credentials, "read_credential", lambda target: None)
    assert youtube_api_key() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager is Windows only")
def test_a_key_saved_by_cmdkey_reads_back_from_credential_manager() -> None:
    """cmdkey /generic stores the password as UTF-16LE; the reader decodes it.

    This writes a fake key under a target of its own with CredWriteW, reads it
    back through the real CredReadW, and deletes it.
    """
    from ctypes import wintypes

    from townrecord.credentials import CRED_TYPE_GENERIC, CREDENTIALW

    target = f"TownRecord/test-{uuid.uuid4().hex}"
    blob = FAKE_KEY.encode("utf-16-le")
    buffer = ctypes.create_string_buffer(blob, len(blob))
    cred = CREDENTIALW()
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = target
    cred.UserName = "youtube"
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    cred.Persist = 1  # CRED_PERSIST_SESSION
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
    advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    assert advapi.CredWriteW(ctypes.byref(cred), 0), ctypes.get_last_error()
    try:
        assert read_windows_credential(target) == FAKE_KEY
    finally:
        advapi.CredDeleteW(target, CRED_TYPE_GENERIC, 0)
    assert read_windows_credential(target) is None
