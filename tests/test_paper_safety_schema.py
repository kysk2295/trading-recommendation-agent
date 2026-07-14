from __future__ import annotations

import sqlite3
from pathlib import Path

from trading_agent.execution_database import _schema_through
from trading_agent.execution_store import ExecutionStore


def test_current_ledger_creates_append_only_paper_safety_tables(
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
    assert {"paper_safety_plans", "paper_safety_actions"} <= tables
    assert "paper_safety_plans_no_update" in triggers
    assert "paper_safety_actions_no_delete" in triggers


def test_v5_ledger_migrates_to_append_only_paper_safety_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(f"{_schema_through(5)}\nPRAGMA user_version = 5;")

    with ExecutionStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        safety_table = connection.execute("SELECT name FROM sqlite_master WHERE name = 'paper_safety_plans'").fetchone()

    assert version == (8,)
    assert safety_table == ("paper_safety_plans",)
