"""API tokens (spec 13.1).

A token is shown to the user once, when it is created. Only its SHA-256 hash
is stored, so a copy of the database does not give anyone a working token.
The tokens are random (about 256 bits), so a plain hash is enough; there is
nothing to guess and no slow hash is needed.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from datetime import UTC, datetime

#: Marks a string as a TownRecord token.
TOKEN_PREFIX = "tr_"

#: The two scopes of spec 13.1.
SCOPE_READ = "read"
SCOPE_READ_WRITE = "read_write"
SCOPES = (SCOPE_READ, SCOPE_READ_WRITE)


def hash_token(token: str) -> str:
    """Return the stored form of a token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """Return a fresh random token."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def create_token(conn: sqlite3.Connection, name: str, scope: str = SCOPE_READ) -> str:
    """Store a token and return the plaintext, which is the only time it is shown."""
    if scope not in SCOPES:
        raise ValueError(f"Scope must be one of {', '.join(SCOPES)}.")
    token = new_token()
    conn.execute(
        "INSERT INTO api_tokens (name, token_hash, scope, created_at) VALUES (?, ?, ?, ?)",
        (name, hash_token(token), scope, datetime.now(UTC).isoformat()),
    )
    return token


def find_token(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """Return the token row for a plaintext token, or None when it does not match."""
    if not token:
        return None
    digest = hash_token(token)
    row = conn.execute(
        "SELECT id, name, token_hash, scope FROM api_tokens WHERE token_hash = ?",
        (digest,),
    ).fetchone()
    if row is None:
        return None
    if not hmac.compare_digest(row["token_hash"], digest):
        return None
    return row
