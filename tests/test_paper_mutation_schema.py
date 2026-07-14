from __future__ import annotations

import sqlite3
from pathlib import Path

from trading_agent.execution_database import _schema_through
from trading_agent.execution_store import ExecutionStore


def test_current_ledger_creates_append_only_mutation_tables(tmp_path: Path) -> None:
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
    assert {"paper_mutation_intents", "paper_mutation_events"} <= tables
    assert "paper_mutation_intents_no_update" in triggers
    assert "paper_mutation_events_no_delete" in triggers


def test_v6_ledger_migrates_to_mutation_schema(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(f"{_schema_through(6)}\nPRAGMA user_version = 6;")

    with ExecutionStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        table = connection.execute("SELECT name FROM sqlite_master WHERE name = 'paper_mutation_intents'").fetchone()

    assert version == (8,)
    assert table == ("paper_mutation_intents",)


def test_v7_mutation_rows_migrate_with_null_entry_source(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(f"{_schema_through(7)}\nPRAGMA user_version = 7;")

    with ExecutionStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        columns = tuple(row[1] for row in connection.execute("PRAGMA table_info(paper_mutation_intents)").fetchall())

    assert version == (8,)
    assert columns[-1] == "entry_intent_id"
