from __future__ import annotations

import json
import sqlite3
import stat
from pathlib import Path

import pytest

from tests.test_autonomous_memory_store import record_fixture
from trading_agent._autonomous_memory_store_sqlite import reader_connection
from trading_agent.autonomous_memory_store import AutonomousMemoryStore, InvalidAutonomousMemoryStoreError


def test_memory_database_is_private_and_reader_is_query_only(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "memory.sqlite3"
    with AutonomousMemoryStore(path).writer() as writer:
        assert writer.append(record_fixture())

    # When / Then
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with reader_connection(path) as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("INSERT INTO autonomous_memories VALUES ('x','x',1,'work','x','x','x')")


def test_reader_rejects_symlink_and_second_writer(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "memory.sqlite3"
    with AutonomousMemoryStore(path).writer() as writer:
        assert writer.append(record_fixture())
        alias = tmp_path / "alias.sqlite3"
        alias.symlink_to(path)

        # When / Then
        with (
            pytest.raises(InvalidAutonomousMemoryStoreError, match="database_write_failed"),
            AutonomousMemoryStore(path).writer(),
        ):
            pass
    with pytest.raises(InvalidAutonomousMemoryStoreError, match="database_path_invalid"):
        AutonomousMemoryStore(alias).reader().history("market.005930.catalyst")


def test_append_only_schema_and_payload_hash_tampering_fail_closed(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "memory.sqlite3"
    record = record_fixture()
    with AutonomousMemoryStore(path).writer() as writer:
        assert writer.append(record)
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM autonomous_memories WHERE memory_id=?", (record.memory_id,))
        connection.execute(
            "INSERT INTO autonomous_memories VALUES (?,?,?,?,?,?,?)",
            ("f" * 64, "market.bad-payload", 1, "market", record.recorded_at.isoformat(), "0" * 64, "{}"),
        )

    # When / Then
    with pytest.raises(InvalidAutonomousMemoryStoreError, match="memory_payload_invalid"):
        AutonomousMemoryStore(path).reader().search(record.scope, ("subject:none",), limit=1)


def test_reader_rejects_valid_payload_with_wrong_sha_and_altered_schema(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "memory.sqlite3"
    record = record_fixture(memory_key="market.005930.sha-test")
    payload = json.dumps(record.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    with AutonomousMemoryStore(path).writer() as writer:
        assert writer.append(record_fixture())
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO autonomous_memories VALUES (?,?,?,?,?,?,?)",
            (
                record.memory_id,
                record.memory_key,
                1,
                record.scope.value,
                record.recorded_at.isoformat(),
                "0" * 64,
                payload,
            ),
        )

    # When / Then
    with pytest.raises(InvalidAutonomousMemoryStoreError, match="memory_payload_invalid"):
        AutonomousMemoryStore(path).reader().history(record.memory_key)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER autonomous_memories_no_update")
    with pytest.raises(InvalidAutonomousMemoryStoreError, match="schema_objects_invalid"):
        AutonomousMemoryStore(path).reader().history(record.memory_key)
