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

    assert version == (9,)
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

    assert version == (9,)
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

    assert version == (9,)
    assert columns[-1] == "entry_intent_id"


def test_v8_mutation_rows_migrate_to_protective_cancel_schema(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(f"{_schema_through(8)}\nPRAGMA user_version = 8;")

    with ExecutionStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        schema: tuple[str] | None = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'paper_mutation_intents'"
        ).fetchone()

    assert version == (9,)
    assert schema is not None
    assert "cancel_protective_oco" in schema[0]


def test_v8_mutation_evidence_is_preserved_exactly_during_v9_migration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    intent_row = (
        "m" * 64,
        "f" * 64,
        "2026-07-14T10:00:00+00:00",
        "submit_entry",
        None,
        None,
        None,
        "r" * 64,
        "AAA",
        None,
        "buy",
        "10",
        "entry-client-1",
    )
    event_row = (
        "e" * 64,
        "m" * 64,
        1,
        "2026-07-14T10:00:01+00:00",
        "attempted",
        None,
        None,
        None,
        "a" * 64,
    )
    with sqlite3.connect(database) as connection:
        connection.executescript(f"{_schema_through(8)}\nPRAGMA user_version = 8;")
        _ = connection.execute(
            "INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "entry-client-1",
                "strategy",
                "v1",
                "AAA",
                "2026-07-14T10:00:00+00:00",
                "buy",
                "10",
                "9",
                "11",
                "12",
                10,
            ),
        )
        _ = connection.execute(
            "INSERT INTO paper_mutation_intents VALUES (" + ",".join("?" for _ in intent_row) + ")",
            intent_row,
        )
        _ = connection.execute(
            """INSERT INTO paper_mutation_events
            (event_key, mutation_key, attempt_number, occurred_at, event_type,
             request_id, status_code, broker_order_id, evidence_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            event_row,
        )
        connection.commit()

    with ExecutionStore(database).writer():
        pass

    with sqlite3.connect(database) as connection:
        migrated_intent = connection.execute("SELECT * FROM paper_mutation_intents").fetchone()
        migrated_event = connection.execute(
            """SELECT event_key, mutation_key, attempt_number, occurred_at,
            event_type, request_id, status_code, broker_order_id, evidence_sha256
            FROM paper_mutation_events"""
        ).fetchone()

    assert migrated_intent == intent_row
    assert migrated_event == event_row
