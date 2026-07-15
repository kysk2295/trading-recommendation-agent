from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from typing import Protocol

from trading_agent.execution_store_errors import InvalidExecutionLedgerGenerationError


@dataclass(frozen=True, slots=True)
class ExecutionLedgerSnapshotIdentity:
    generation: int
    sha256: str


class _DigestWriter(Protocol):
    def update(self, data: bytes, /) -> None: ...


def read_execution_ledger_snapshot_identity(
    connection: sqlite3.Connection,
) -> ExecutionLedgerSnapshotIdentity:
    digest = hashlib.sha256()
    version: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    if version is None:
        raise InvalidExecutionLedgerGenerationError
    _hash_scalar(digest, version[0])
    tables: list[tuple[str, str | None]] = connection.execute(
        """SELECT name, sql FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"""
    ).fetchall()
    generation = 0
    for table, schema_sql in tables:
        _hash_frame(digest, b"T", table.encode())
        _hash_frame(
            digest,
            b"D",
            ("" if schema_sql is None else " ".join(schema_sql.split())).encode(),
        )
        quoted = table.replace('"', '""')
        cursor = connection.execute(f'SELECT rowid, * FROM "{quoted}" ORDER BY rowid')
        for row in cursor:
            _hash_frame(digest, b"R", b"")
            generation += 1
            for value in row:
                _hash_scalar(digest, value)
    return ExecutionLedgerSnapshotIdentity(generation, digest.hexdigest())


def _hash_scalar(digest: _DigestWriter, value: object) -> None:
    if value is None:
        _hash_frame(digest, b"N", b"")
    elif isinstance(value, bool):
        _hash_frame(digest, b"I", str(int(value)).encode())
    elif isinstance(value, int):
        _hash_frame(digest, b"I", str(value).encode())
    elif isinstance(value, float):
        _hash_frame(digest, b"F", value.hex().encode())
    elif isinstance(value, str):
        _hash_frame(digest, b"S", value.encode())
    elif isinstance(value, bytes):
        _hash_frame(digest, b"B", value)
    else:
        raise InvalidExecutionLedgerGenerationError


def _hash_frame(digest: _DigestWriter, tag: bytes, payload: bytes) -> None:
    digest.update(tag)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
