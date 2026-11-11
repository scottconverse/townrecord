"""API keys, read from the operating system's credential store (spec 17).

Spec 17: "API keys go in the operating system's credential store (Windows
Credential Manager, macOS Keychain)" and "An environment variable overrides a
saved key." So a key is looked up in that order: the environment variable,
then the store. Nothing here prints, logs or returns a key anywhere but to its
caller; log lines name the target and the error code, never the value.

On Windows the store is read with ``CredReadW`` through ctypes, so no new
dependency is needed. A generic credential saved with
``cmdkey /generic:TownRecord/youtube-data-api /user:youtube /pass:...`` holds
its password as UTF-16LE, and that is how the blob is decoded. Other
platforms have no store reader yet and use the environment variable.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from collections.abc import Callable, Mapping
from ctypes import wintypes
from typing import Any

logger = logging.getLogger(__name__)

#: The Credential Manager target that holds the YouTube Data API key.
YOUTUBE_API_KEY_TARGET = "TownRecord/youtube-data-api"

#: The environment variable that overrides the saved key (spec 17).
YOUTUBE_API_KEY_ENV = "TOWNRECORD_YOUTUBE_API_KEY"

#: ``CRED_TYPE_GENERIC``: a credential saved by ``cmdkey /generic``.
CRED_TYPE_GENERIC = 1

#: ``ERROR_NOT_FOUND``: the store has no credential under that target.
ERROR_NOT_FOUND = 1168


class CREDENTIALW(ctypes.Structure):
    """The Win32 ``CREDENTIALW`` structure that ``CredReadW`` fills in."""

    _fields_ = [  # noqa: RUF012 -- ctypes reads this class attribute
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class _Advapi:
    """``advapi32``'s two credential calls, typed, and the thread's last error."""

    def __init__(self) -> None:
        dll = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
        self.CredReadW = dll.CredReadW
        self.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
        ]
        self.CredReadW.restype = wintypes.BOOL
        self.CredFree = dll.CredFree
        self.CredFree.argtypes = [ctypes.c_void_p]
        self.CredFree.restype = None

    def last_error(self) -> int:
        return ctypes.get_last_error()  # type: ignore[attr-defined]


def _decode(blob: bytes) -> str:
    """The password of a generic credential: UTF-16LE as cmdkey writes it."""
    if len(blob) % 2 == 0:
        try:
            return blob.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    return blob.decode("utf-8", errors="strict")


def read_windows_credential(target: str, *, api: Any = None) -> str | None:
    """Return the password of the generic credential `target`, or None.

    None when the store has no such credential (``ERROR_NOT_FOUND``), and also
    when the store refuses for another reason; that one is logged with its
    error code. `api` is for tests: an object with ``CredReadW``, ``CredFree``
    and ``last_error``.
    """
    if api is None:
        if sys.platform != "win32":
            return None
        api = _Advapi()
    pointer = ctypes.POINTER(CREDENTIALW)()
    if not api.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = api.last_error()
        if error != ERROR_NOT_FOUND:
            logger.warning("The credential store could not read %s (error %s).", target, error)
        return None
    try:
        cred = pointer.contents
        blob = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
    finally:
        api.CredFree(pointer)
    value = _decode(blob).strip().strip("\x00").strip()
    return value or None


#: How the store is read. A module attribute, so tests replace it and no test
#: ever reads the machine's real credential.
read_credential: Callable[[str], str | None] = read_windows_credential


def youtube_api_key(environ: Mapping[str, str] | None = None) -> str | None:
    """The YouTube Data API key: the environment variable, then the store, or None."""
    env = os.environ if environ is None else environ
    from_env = (env.get(YOUTUBE_API_KEY_ENV) or "").strip()
    if from_env:
        return from_env
    return read_credential(YOUTUBE_API_KEY_TARGET)
