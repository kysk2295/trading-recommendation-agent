from __future__ import annotations

import sqlite3
from pathlib import Path

from trading_agent.execution_errors import (
    ExecutionSchemaIntegrityError,
    UnsupportedExecutionSchemaError,
)
from trading_agent.execution_schema import CREATE_SCHEMA, SCHEMA_VERSION
from trading_agent.trade_update_schema import CREATE_TRADE_UPDATE_SCHEMA


def prepare_execution_writer_connection(
    connection: sqlite3.Connection,
    path: Path,
) -> None:
    _ = connection.execute("PRAGMA foreign_keys = ON")
    _ = connection.execute("PRAGMA busy_timeout = 0")
    _ = connection.execute("PRAGMA journal_mode = WAL").fetchone()
    version_row: tuple[int] | None = connection.execute(
        "PRAGMA user_version"
    ).fetchone()
    version = 0 if version_row is None else version_row[0]
    if version not in (0, 1, SCHEMA_VERSION):
        raise UnsupportedExecutionSchemaError(path, version)
    if version == 0:
        unexpected = _schema_object_names(connection)
        if unexpected:
            raise ExecutionSchemaIntegrityError(
                path,
                tuple(f"unexpected-v0:{name}" for name in unexpected),
            )
    if version == SCHEMA_VERSION:
        _require_v2_schema(connection, path)
        return
    schema = (
        f"{CREATE_SCHEMA}\n{CREATE_TRADE_UPDATE_SCHEMA}"
        if version == 0
        else CREATE_TRADE_UPDATE_SCHEMA
    )
    try:
        connection.executescript(
            f"BEGIN IMMEDIATE;\n{schema}\n"
            f"PRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
        )
    except sqlite3.Error:
        connection.rollback()
        raise
    _require_v2_schema(connection, path)


def _require_v2_schema(connection: sqlite3.Connection, path: Path) -> None:
    required = frozenset(
        (
            "account_binding",
            "order_intents",
            "broker_order_events",
            "trade_update_events",
            "order_intents_no_update",
            "order_intents_no_delete",
            "broker_events_no_update",
            "broker_events_no_delete",
            "account_binding_no_update",
            "account_binding_no_delete",
            "trade_update_events_no_update",
            "trade_update_events_no_delete",
            "trade_update_execution_id_unique",
        )
    )
    expected = _expected_v2_schema_definitions()
    actual = _schema_object_definitions(connection)
    invalid = tuple(
        sorted(
            name if name not in actual else f"invalid:{name}"
            for name in required
            if actual.get(name) != expected.get(name)
        )
    )
    if invalid:
        raise ExecutionSchemaIntegrityError(path, invalid)


def _schema_object_names(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows: list[tuple[str]] = connection.execute(
        """SELECT name FROM sqlite_master
        WHERE type IN ('table', 'trigger', 'index')
          AND name NOT LIKE 'sqlite_%' ORDER BY name"""
    ).fetchall()
    return tuple(row[0] for row in rows)


def _expected_v2_schema_definitions() -> dict[str, tuple[str, str]]:
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(f"{CREATE_SCHEMA}\n{CREATE_TRADE_UPDATE_SCHEMA}")
        return _schema_object_definitions(connection)


def _schema_object_definitions(
    connection: sqlite3.Connection,
) -> dict[str, tuple[str, str]]:
    rows: list[tuple[str, str, str | None]] = connection.execute(
        "SELECT name, type, sql FROM sqlite_master "
        "WHERE type IN ('table', 'trigger', 'index') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return {
        name: (object_type, _normalize_schema_sql(sql))
        for name, object_type, sql in rows
    }


def _normalize_schema_sql(sql: str | None) -> str:
    return "" if sql is None else " ".join(sql.split())
