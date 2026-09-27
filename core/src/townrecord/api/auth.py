"""Bearer token checking.

Every route needs a token, including `/docs`, `/redoc` and `/openapi.json`
(spec 13.1 and decision 10 rule 2). No route is public.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request, status

from ..db import connect
from .tokens import SCOPE_READ_WRITE, find_token

#: Methods that change data. They need the read_write scope.
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def get_connection(request: Request) -> Iterator[sqlite3.Connection]:
    """Open one database connection for one request."""
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _unauthorized(reason: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=reason,
        headers={"WWW-Authenticate": "Bearer"},
    )


def bearer_token(header: str | None) -> str | None:
    """Return the token from an Authorization header, or None if it is malformed."""
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def require_token(
    request: Request,
    conn: sqlite3.Connection = Depends(get_connection),
) -> sqlite3.Row:
    """Reject the request unless it carries a valid token of a usable scope."""
    token = bearer_token(request.headers.get("authorization"))
    if token is None:
        raise _unauthorized("A bearer token is required.")
    row = find_token(conn, token)
    if row is None:
        raise _unauthorized("The bearer token is not valid.")
    if request.method in WRITE_METHODS and row["scope"] != SCOPE_READ_WRITE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This token has read-only scope.",
        )
    return row
