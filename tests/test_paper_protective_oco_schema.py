from __future__ import annotations

import sqlite3
from pathlib import Path

from trading_agent.execution_store import ExecutionStore


def test_current_ledger_creates_append_only_protective_oco_tables(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    with ExecutionStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        tables = frozenset(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        )
        triggers = frozenset(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
        )

    assert version == (8,)
    assert "protective_oco_plans" in tables
    assert "paper_recovery_protective_oco_legs" in tables
    assert "protective_oco_plans_no_update" in triggers
    assert "paper_recovery_protective_oco_legs_no_delete" in triggers
