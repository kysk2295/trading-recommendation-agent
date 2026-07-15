from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
    intent,
)
from trading_agent.execution_errors import ExecutionSchemaIntegrityError
from trading_agent.execution_schema import SCHEMA_VERSION
from trading_agent.execution_store import ExecutionStore
from trading_agent.execution_store_errors import InvalidExecutionLedgerGenerationError


def test_execution_identity_is_stable_across_readers_and_wal_checkpoint(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)

    first = store.ledger_snapshot_identity()
    second = ExecutionStore(store.path).ledger_snapshot_identity()
    with sqlite3.connect(store.path) as connection:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    after_checkpoint = store.ledger_snapshot_identity()

    assert first == second == after_checkpoint
    assert first.generation > 0
    assert len(first.sha256) == 64
    assert checkpoint is not None


def test_execution_identity_changes_after_an_append(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.bind_account(FINGERPRINT, OBSERVED_AT)
    before = store.ledger_snapshot_identity()

    with store.writer() as writer:
        _ = writer.save_intent(intent(), quantity=100)
    after = store.ledger_snapshot_identity()

    assert after.generation == before.generation + 1
    assert after.sha256 != before.sha256


def test_execution_identity_does_not_create_a_missing_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing" / "execution.sqlite3"

    with pytest.raises(InvalidExecutionLedgerGenerationError):
        _ = ExecutionStore(database).ledger_snapshot_identity()

    assert not database.exists()
    assert not database.parent.exists()


def test_execution_identity_rejects_current_version_with_invalid_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "invalid.sqlite3"
    with sqlite3.connect(database) as connection:
        _ = connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    with pytest.raises(ExecutionSchemaIntegrityError):
        _ = ExecutionStore(database).ledger_snapshot_identity()
