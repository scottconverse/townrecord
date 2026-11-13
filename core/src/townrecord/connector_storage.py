"""Shared SDK Storage over the application's migrated SQLite connection.

The caller owns connection lifetime and threading. Atomic destination updates may
enclose Storage calls in ``atomic``; nested calls use savepoints, never commits.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import BinaryIO

from civic_data_connectors.extensions import StorageConflict, StoredValue
from civic_data_connectors.storage import Mutation


@contextmanager
def atomic(conn: sqlite3.Connection) -> Iterator[None]:
    nested = conn.in_transaction
    name = "connector_" + uuid.uuid4().hex
    conn.execute(f"SAVEPOINT {name}" if nested else "BEGIN IMMEDIATE")
    try:
        yield
        conn.execute(f"RELEASE {name}" if nested else "COMMIT")
    except BaseException:
        if nested:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
        else:
            conn.execute("ROLLBACK")
        raise


class AppStorage:
    """Durable scoped CAS, history and bounded streaming without another DB."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def put(
        self,
        collection: str,
        key: str,
        value: dict,
        *,
        scope: str,
        expected_version: str | None = None,
        create_only: bool = False,
    ) -> str:
        return self.put_many(
            [Mutation(collection, key, value, expected_version, create_only)], scope=scope
        )[0]

    def put_many(self, mutations: list[Mutation], *, scope: str) -> list[str]:
        with atomic(self.conn):
            return [self._put(mutation, scope) for mutation in mutations]

    def _put(self, mutation: Mutation, scope: str) -> str:
        if not all(isinstance(s, str) and s for s in (scope, mutation.collection, mutation.key)):
            raise ValueError("Storage scope, collection and key must be nonempty strings.")
        if not isinstance(mutation.value, dict):
            raise ValueError("Storage values must be dictionaries.")
        value = json.dumps(mutation.value, ensure_ascii=False, allow_nan=False)
        row = self.conn.execute(
            "SELECT version FROM connector_state WHERE scope=? AND collection=? AND key=?",
            (scope, mutation.collection, mutation.key),
        ).fetchone()
        if mutation.create_only and row:
            raise StorageConflict("The scoped key already exists.")
        if mutation.expected_version is not None and (
            row is None or str(row[0]) != mutation.expected_version
        ):
            raise StorageConflict("The stored version changed or the scoped key is absent.")
        version = row[0] + 1 if row else 1
        self.conn.execute(
            "INSERT INTO connector_state VALUES (?,?,?,?,?) "
            "ON CONFLICT(scope,collection,key) DO UPDATE "
            "SET value=excluded.value,version=excluded.version",
            (scope, mutation.collection, mutation.key, value, version),
        )
        self.conn.execute(
            "INSERT INTO connector_state_history VALUES (?,?,?,?,?)",
            (scope, mutation.collection, mutation.key, version, value),
        )
        return str(version)

    def get(self, collection: str, key: str, *, scope: str) -> StoredValue | None:
        row = self.conn.execute(
            "SELECT value,version FROM connector_state WHERE scope=? AND collection=? AND key=?",
            (scope, collection, key),
        ).fetchone()
        return None if row is None else StoredValue(key, json.loads(row[0]), str(row[1]))

    def iter_values(self, collection: str, *, scope: str) -> Iterator[StoredValue]:
        for row in self.conn.execute(
            "SELECT key,value,version FROM connector_state "
            "WHERE scope=? AND collection=? ORDER BY key",
            (scope, collection),
        ):
            yield StoredValue(row[0], json.loads(row[1]), str(row[2]))

    def list(self, collection: str, *, scope: str) -> list[StoredValue]:
        return list(self.iter_values(collection, scope=scope))

    def history(self, collection: str, key: str, *, scope: str) -> list[StoredValue]:
        return [
            StoredValue(key, json.loads(row[0]), str(row[1]))
            for row in self.conn.execute(
                "SELECT value,version FROM connector_state_history "
                "WHERE scope=? AND collection=? AND key=? ORDER BY version",
                (scope, collection, key),
            )
        ]

    def save_capture(self, stream: BinaryIO, *, scope: str, max_bytes: int) -> dict:
        if not isinstance(scope, str) or not scope or type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("A scoped capture requires a positive transfer limit.")
        digest, size = hashlib.sha256(), 0
        with tempfile.TemporaryFile() as staging:
            while chunk := stream.read(min(1024 * 1024, max_bytes - size + 1)):
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("The capture exceeds the transfer byte limit.")
                digest.update(chunk)
                staging.write(chunk)
            sha = digest.hexdigest()
            staging.seek(0)
            with atomic(self.conn):
                found = self.conn.execute(
                    "SELECT id FROM connector_captures WHERE scope=? AND sha256=?", (scope, sha)
                ).fetchone()
                if found is None:
                    cursor = self.conn.execute(
                        "INSERT INTO connector_captures(scope,sha256,size,content) "
                        "VALUES (?,?,?,zeroblob(?))",
                        (scope, sha, size, size),
                    )
                    if size:
                        with self.conn.blobopen(
                            "connector_captures", "content", cursor.lastrowid
                        ) as blob:
                            while chunk := staging.read(1024 * 1024):
                                blob.write(chunk)
        return {"kind": "townrecord_capture", "sha256": sha, "byte_length": size}

    def open_capture(self, reference: dict, *, scope: str) -> BinaryIO:
        if reference.get("kind") != "townrecord_capture":
            raise ValueError("The capture storage reference is not supported.")
        stream = tempfile.TemporaryFile()  # noqa: SIM115 - caller owns the returned stream
        try:
            row = self.conn.execute(
                "SELECT id,size FROM connector_captures WHERE scope=? AND sha256=?",
                (scope, reference.get("sha256")),
            ).fetchone()
            if row is None:
                raise ValueError("The capture is unavailable in the caller's scope.")
            if reference.get("byte_length", row[1]) != row[1]:
                raise ValueError("The capture reference byte length does not match.")
            if row[1]:
                with self.conn.blobopen(
                    "connector_captures", "content", row[0], readonly=True
                ) as blob:
                    while chunk := blob.read(1024 * 1024):
                        stream.write(chunk)
            stream.seek(0)
            return stream
        except BaseException:
            stream.close()
            raise
