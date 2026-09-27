"""Shared test fixtures."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from townrecord.api.app import create_app
from townrecord.api.tokens import SCOPE_READ, SCOPE_READ_WRITE, create_token
from townrecord.config import Settings
from townrecord.db import connect, migrate

TEST_VERSION = "0.1.0-test"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A database file that does not exist yet."""
    return tmp_path / "townrecord.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """A migrated database connection."""
    connection = connect(db_path)
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def read_token(conn: sqlite3.Connection) -> str:
    """A token with the read scope."""
    return create_token(conn, "reader", SCOPE_READ)


@pytest.fixture
def read_write_token(conn: sqlite3.Connection) -> str:
    """A token with the read_write scope."""
    return create_token(conn, "writer", SCOPE_READ_WRITE)


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    """A test client for the API, using a temporary database."""
    app = create_app(Settings(db_path=db_path, version=TEST_VERSION))
    with TestClient(app) as test_client:
        yield test_client
