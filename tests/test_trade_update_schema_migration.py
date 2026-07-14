from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
    intent,
    trade_update,
)
from trading_agent.execution_errors import ExecutionSchemaIntegrityError
from trading_agent.execution_schema import CREATE_SCHEMA
from trading_agent.execution_store import ExecutionStore


def test_v1_ledger_migrates_without_losing_rows(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(CREATE_SCHEMA)
        _ = connection.execute("PRAGMA user_version = 1")
        _ = connection.execute(
            """INSERT INTO account_binding
            (binding_id, account_fingerprint, bound_at) VALUES (1, ?, ?)""",
            (FINGERPRINT, OBSERVED_AT.isoformat()),
        )
        values = (
            intent().intent_id,
            intent().strategy_id,
            intent().strategy_version,
            intent().symbol,
            intent().created_at.isoformat(),
            intent().side.value,
            str(intent().entry_limit),
            str(intent().stop),
            str(intent().target_1r),
            str(intent().target_2r),
            100,
        )
        _ = connection.execute(
            "INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        connection.commit()

    store = ExecutionStore(database)
    with store.writer() as writer:
        inserted = writer.append_trade_update(
            trade_update(),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )

    assert inserted is True
    assert store.is_initialized() is True
    assert store.account_fingerprint() == FINGERPRINT
    assert store.intents()[0].intent_id == intent().intent_id
    assert len(store.trade_updates(intent().intent_id)) == 1


def test_v2_marker_without_v2_tables_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(CREATE_SCHEMA)
        _ = connection.execute("PRAGMA user_version = 2")
        connection.commit()

    with (
        pytest.raises(ExecutionSchemaIntegrityError, match="무결성"),
        ExecutionStore(database).writer(),
    ):
        pytest.fail("손상된 v2 원장이 writer를 열면 안 됩니다")


@pytest.mark.parametrize(
    "mutation",
    (
        "DROP TRIGGER trade_update_events_no_update; "
        "CREATE TRIGGER trade_update_events_no_update "
        "BEFORE UPDATE ON trade_update_events BEGIN SELECT 1; END;",
        "DROP INDEX trade_update_execution_id_unique; "
        "CREATE INDEX trade_update_execution_id_unique "
        "ON trade_update_events(execution_id);",
    ),
)
def test_v2_same_name_but_weakened_objects_fail_closed(
    tmp_path: Path,
    mutation: str,
) -> None:
    database = tmp_path / "execution.sqlite3"
    with ExecutionStore(database).writer():
        pass
    with sqlite3.connect(database) as connection:
        connection.executescript(mutation)

    with (
        pytest.raises(ExecutionSchemaIntegrityError, match="무결성"),
        ExecutionStore(database).writer(),
    ):
        pytest.fail("정의가 약화된 v2 원장이 writer를 열면 안 됩니다")


def test_nonempty_unversioned_ledger_fails_closed(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        _ = connection.execute("CREATE TABLE unknown_ledger (value TEXT)")
        connection.commit()

    with (
        pytest.raises(ExecutionSchemaIntegrityError, match="무결성"),
        ExecutionStore(database).writer(),
    ):
        pytest.fail("스키마를 알 수 없는 v0 원장을 승격하면 안 됩니다")
    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()

    assert version == (0,)


def test_failed_v1_migration_rolls_back_version(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(CREATE_SCHEMA)
        _ = connection.execute("CREATE TABLE trade_update_events (bad TEXT)")
        _ = connection.execute("PRAGMA user_version = 1")
        connection.commit()

    with pytest.raises(sqlite3.OperationalError), ExecutionStore(database).writer():
        pytest.fail("깨진 v1 원장이 마이그레이션되면 안 됩니다")
    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()

    assert version == (1,)


def test_trade_update_table_is_append_only(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_trade_update(
            trade_update(),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )

    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute(
                "UPDATE trade_update_events SET order_status = 'filled'"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute("DELETE FROM trade_update_events")
