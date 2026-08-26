from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import stat
from multiprocessing.synchronize import Event
from pathlib import Path

import pytest

import trading_agent._autonomous_memory_store_sqlite as memory_sqlite
from tests.test_autonomous_memory_store import record_fixture
from trading_agent._autonomous_memory_store_sqlite import reader_connection
from trading_agent.autonomous_memory_store import AutonomousMemoryStore, InvalidAutonomousMemoryStoreError


def _hold_writer(path: str, ready: Event, release: Event) -> None:
    with AutonomousMemoryStore(Path(path)).writer():
        ready.set()
        assert release.wait(10)


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


def test_schema_has_exact_v1_columns_and_memory_key_version_uniqueness(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    with AutonomousMemoryStore(path).writer() as writer:
        assert writer.append(record_fixture())
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        assert tuple(row[1] for row in connection.execute("PRAGMA table_info(autonomous_memories)")) == (
            "memory_id",
            "memory_key",
            "version",
            "scope",
            "recorded_at",
            "payload_sha256",
            "payload_json",
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO autonomous_memories VALUES (?,?,?,?,?,?,?)",
                ("a" * 64, "market.005930.catalyst", 1, "market", "x", "x", "{}"),
            )


def test_reader_rejects_mode_and_parent_path_that_are_no_longer_private(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    with AutonomousMemoryStore(path).writer() as writer:
        assert writer.append(record_fixture())
    os.chmod(path, 0o644)
    with pytest.raises(InvalidAutonomousMemoryStoreError, match="database_path_invalid"):
        AutonomousMemoryStore(path).reader().history("market.005930.catalyst")
    os.chmod(path, 0o600)
    os.chmod(tmp_path, 0o755)
    with pytest.raises(InvalidAutonomousMemoryStoreError, match="database_read_failed"):
        AutonomousMemoryStore(path).reader().history("market.005930.catalyst")


def test_second_writer_lease_is_rejected_across_a_real_child_process(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    with AutonomousMemoryStore(path).writer() as writer:
        assert writer.append(record_fixture())
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(target=_hold_writer, args=(str(path), ready, release))
    process.start()
    try:
        assert ready.wait(10)
        with (
            pytest.raises(InvalidAutonomousMemoryStoreError, match="database_write_failed"),
            AutonomousMemoryStore(path).writer(),
        ):
            pass
    finally:
        release.set()
        process.join(timeout=10)
    assert process.exitcode == 0


def test_open_reader_keeps_prior_descriptor_snapshot_during_generation_replacement(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite3"
    first = record_fixture()
    second = record_fixture(
        version=2,
        summary="A second durable memory generation preserves the prior reader descriptor snapshot.",
    )
    with AutonomousMemoryStore(path).writer() as writer:
        assert writer.append(first)
    with reader_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM autonomous_memories").fetchone() == (1,)
        with AutonomousMemoryStore(path).writer() as writer:
            assert writer.append(second)
        assert connection.execute("SELECT COUNT(*) FROM autonomous_memories").fetchone() == (1,)
    assert AutonomousMemoryStore(path).reader().history(first.memory_key) == (first, second)


def test_generation_replacement_rejects_path_swap_after_atomic_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "memory.sqlite3"
    attacker = tmp_path / "attacker.sqlite3"
    with AutonomousMemoryStore(path).writer() as writer:
        assert writer.append(record_fixture())
    with AutonomousMemoryStore(attacker).writer() as writer:
        assert writer.append(record_fixture(memory_key="market.005930.attacker"))
    attacker_before = attacker.read_bytes()
    original_replace = memory_sqlite.replace_sqlite_database

    def replace_then_swap(parent: int, name: str, payload: bytes) -> None:
        original_replace(parent, name, payload)
        path.replace(tmp_path / "held.sqlite3")
        attacker.replace(path)

    monkeypatch.setattr(memory_sqlite, "replace_sqlite_database", replace_then_swap)
    with (
        pytest.raises(InvalidAutonomousMemoryStoreError, match="database_write_failed"),
        AutonomousMemoryStore(path).writer() as writer,
    ):
        assert writer.append(record_fixture(memory_key="market.005930.next"))
    assert path.read_bytes() == attacker_before
