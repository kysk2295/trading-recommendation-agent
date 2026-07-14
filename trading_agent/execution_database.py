from __future__ import annotations

import sqlite3
from pathlib import Path

from trading_agent.execution_errors import (
    ExecutionSchemaIntegrityError,
    UnsupportedExecutionSchemaError,
)
from trading_agent.execution_schema import CREATE_SCHEMA, SCHEMA_VERSION
from trading_agent.paper_account_activity_schema import (
    CREATE_PAPER_ACCOUNT_ACTIVITY_SCHEMA,
    MIGRATE_PAPER_RECOVERY_V3_TO_V4,
)
from trading_agent.paper_mutation_schema import (
    CREATE_PAPER_MUTATION_SCHEMA,
    CREATE_PAPER_MUTATION_SCHEMA_V7,
    MIGRATE_PAPER_MUTATION_V7_TO_V8,
)
from trading_agent.paper_protective_oco_schema import (
    CREATE_PAPER_PROTECTIVE_OCO_SCHEMA,
)
from trading_agent.paper_safety_schema import CREATE_PAPER_SAFETY_SCHEMA
from trading_agent.paper_stream_recovery import CREATE_PAPER_STREAM_RECOVERY_SCHEMA
from trading_agent.paper_stream_recovery_schema import (
    CREATE_PAPER_STREAM_RECOVERY_SCHEMA_V3,
)
from trading_agent.trade_update_receipt_schema import CREATE_TRADE_UPDATE_RECEIPT_SCHEMA
from trading_agent.trade_update_schema import CREATE_TRADE_UPDATE_SCHEMA


def prepare_execution_writer_connection(
    connection: sqlite3.Connection,
    path: Path,
) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    _ = connection.execute("PRAGMA busy_timeout = 0")
    _ = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    version_row: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    version = 0 if version_row is None else version_row[0]
    if version not in (0, 1, 2, 3, 4, 5, 6, 7, SCHEMA_VERSION):
        raise UnsupportedExecutionSchemaError(path, version)
    if version == 0:
        unexpected = _schema_object_names(connection)
        if unexpected:
            raise ExecutionSchemaIntegrityError(
                path,
                tuple(f"unexpected-v0:{name}" for name in unexpected),
            )
    if version > 0:
        _require_schema_version(connection, path, version)
    if version == SCHEMA_VERSION:
        return
    schema = _migration_schema(version)
    try:
        connection.executescript(f"BEGIN IMMEDIATE;\n{schema}\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;")
    except sqlite3.Error:
        connection.rollback()
        raise
    _require_schema_version(connection, path, SCHEMA_VERSION)


def require_current_execution_schema(
    connection: sqlite3.Connection,
    path: Path,
) -> None:
    version_row: tuple[int] | None = connection.execute("PRAGMA user_version").fetchone()
    version = 0 if version_row is None else version_row[0]
    if version != SCHEMA_VERSION:
        raise UnsupportedExecutionSchemaError(path, version)
    _require_schema_version(connection, path, SCHEMA_VERSION)


def _require_schema_version(
    connection: sqlite3.Connection,
    path: Path,
    version: int,
) -> None:
    expected = _expected_schema_definitions(version)
    actual = _schema_object_definitions(connection)
    invalid = sorted(
        name if name not in actual else f"invalid:{name}" for name in expected if actual.get(name) != expected[name]
    )
    invalid.extend(f"unexpected:{name}" for name in actual if name not in expected)
    if invalid:
        raise ExecutionSchemaIntegrityError(path, tuple(sorted(invalid)))


def _schema_object_names(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows: list[tuple[str]] = connection.execute(
        """SELECT name FROM sqlite_master
        WHERE type IN ('table', 'trigger', 'index')
          AND name NOT LIKE 'sqlite_%' ORDER BY name"""
    ).fetchall()
    return tuple(row[0] for row in rows)


def _expected_schema_definitions(version: int) -> dict[str, tuple[str, str]]:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(_schema_through(version))
        return _schema_object_definitions(connection)


def _schema_through(version: int) -> str:
    if version == 1:
        return CREATE_SCHEMA
    if version == 2:
        return f"{CREATE_SCHEMA}\n{CREATE_TRADE_UPDATE_SCHEMA}"
    if version == 3:
        return (
            f"{CREATE_SCHEMA}\n{CREATE_TRADE_UPDATE_SCHEMA}\n"
            f"{CREATE_TRADE_UPDATE_RECEIPT_SCHEMA}\n"
            f"{CREATE_PAPER_STREAM_RECOVERY_SCHEMA_V3}"
        )
    if version == 4:
        return (
            f"{CREATE_SCHEMA}\n{CREATE_TRADE_UPDATE_SCHEMA}\n"
            f"{CREATE_TRADE_UPDATE_RECEIPT_SCHEMA}\n"
            f"{CREATE_PAPER_STREAM_RECOVERY_SCHEMA}\n"
            f"{CREATE_PAPER_ACCOUNT_ACTIVITY_SCHEMA}"
        )
    if version == 5:
        return f"{_schema_through(4)}\n{CREATE_PAPER_PROTECTIVE_OCO_SCHEMA}"
    if version == 6:
        return f"{_schema_through(5)}\n{CREATE_PAPER_SAFETY_SCHEMA}"
    if version == 7:
        return f"{_schema_through(6)}\n{CREATE_PAPER_MUTATION_SCHEMA_V7}"
    if version == SCHEMA_VERSION:
        return f"{_schema_through(6)}\n{CREATE_PAPER_MUTATION_SCHEMA}"
    raise ValueError(version)


def _migration_schema(version: int) -> str:
    if version == 0:
        return _schema_through(SCHEMA_VERSION)
    if version == 1:
        return (
            f"{CREATE_TRADE_UPDATE_SCHEMA}\n{CREATE_TRADE_UPDATE_RECEIPT_SCHEMA}\n"
            f"{CREATE_PAPER_STREAM_RECOVERY_SCHEMA}\n"
            f"{CREATE_PAPER_ACCOUNT_ACTIVITY_SCHEMA}\n"
            f"{CREATE_PAPER_PROTECTIVE_OCO_SCHEMA}\n{CREATE_PAPER_SAFETY_SCHEMA}\n"
            f"{CREATE_PAPER_MUTATION_SCHEMA}"
        )
    if version == 2:
        return (
            f"{CREATE_TRADE_UPDATE_RECEIPT_SCHEMA}\n"
            f"{CREATE_PAPER_STREAM_RECOVERY_SCHEMA}\n"
            f"{CREATE_PAPER_ACCOUNT_ACTIVITY_SCHEMA}\n"
            f"{CREATE_PAPER_PROTECTIVE_OCO_SCHEMA}\n{CREATE_PAPER_SAFETY_SCHEMA}\n"
            f"{CREATE_PAPER_MUTATION_SCHEMA}"
        )
    if version == 3:
        return (
            f"{MIGRATE_PAPER_RECOVERY_V3_TO_V4}\n{CREATE_PAPER_PROTECTIVE_OCO_SCHEMA}\n"
            f"{CREATE_PAPER_SAFETY_SCHEMA}\n{CREATE_PAPER_MUTATION_SCHEMA}"
        )
    if version == 4:
        return f"{CREATE_PAPER_PROTECTIVE_OCO_SCHEMA}\n{CREATE_PAPER_SAFETY_SCHEMA}\n{CREATE_PAPER_MUTATION_SCHEMA}"
    if version == 5:
        return f"{CREATE_PAPER_SAFETY_SCHEMA}\n{CREATE_PAPER_MUTATION_SCHEMA}"
    if version == 6:
        return CREATE_PAPER_MUTATION_SCHEMA
    if version == 7:
        return MIGRATE_PAPER_MUTATION_V7_TO_V8
    raise ValueError(version)


def _schema_object_definitions(
    connection: sqlite3.Connection,
) -> dict[str, tuple[str, str]]:
    rows: list[tuple[str, str, str | None]] = connection.execute(
        "SELECT name, type, sql FROM sqlite_master "
        "WHERE type IN ('table', 'trigger', 'index') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return {name: (object_type, _normalize_schema_sql(sql)) for name, object_type, sql in rows}


def _normalize_schema_sql(sql: str | None) -> str:
    return "" if sql is None else " ".join(sql.split())
