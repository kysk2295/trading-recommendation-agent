from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

import trading_agent.autonomous_memory_store as memory_store
from tests.test_autonomous_memory_store import record_fixture
from trading_agent._autonomous_memory_store_sqlite import DatabaseIdentity
from trading_agent.autonomous_memory_store import AutonomousMemoryStore, InvalidAutonomousMemoryStoreError


def test_post_replace_flush_failure_reconciles_durable_memory_for_exact_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "memory.sqlite3"
    record = record_fixture()
    original_flush = memory_store.flush_generation

    def flush_then_fail(identity: DatabaseIdentity, connection: sqlite3.Connection) -> None:
        original_flush(identity, connection)
        raise OSError

    with AutonomousMemoryStore(path).writer() as writer:
        monkeypatch.setattr(memory_store, "flush_generation", flush_then_fail)
        with pytest.raises(InvalidAutonomousMemoryStoreError, match="writer_generation_flush_failed"):
            writer.append(record)
        assert writer.append(record) is False
    assert AutonomousMemoryStore(path).reader().history(record.memory_key) == (record,)


def test_flush_and_reconcile_failure_deactivates_writer_without_claiming_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "memory.sqlite3"
    record = record_fixture()

    def fail_generation(identity: DatabaseIdentity, connection: sqlite3.Connection) -> None:
        raise OSError

    with AutonomousMemoryStore(path).writer() as writer:
        monkeypatch.setattr(memory_store, "flush_generation", fail_generation)
        monkeypatch.setattr(memory_store, "reconcile_generation", fail_generation)
        with pytest.raises(InvalidAutonomousMemoryStoreError, match="writer_generation_reconcile_failed"):
            writer.append(record)
        with pytest.raises(InvalidAutonomousMemoryStoreError, match="writer_closed"):
            writer.append(record)
    assert AutonomousMemoryStore(path).reader().history(record.memory_key) == ()


def test_writer_rejects_parent_identity_swap_after_lease_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "memory.sqlite3"
    original_lease = memory_store.writer_lease

    @contextmanager
    def lease_then_swap(lease_path: Path, parent: int) -> Iterator[None]:
        with original_lease(lease_path, parent):
            yield
        held = lease_path.parent.parent / "held-memory-parent"
        attacker = lease_path.parent.parent / "attacker-memory-parent"
        lease_path.parent.replace(held)
        attacker.mkdir(mode=0o700)
        attacker.replace(lease_path.parent)

    monkeypatch.setattr(memory_store, "writer_lease", lease_then_swap)
    with (
        pytest.raises(InvalidAutonomousMemoryStoreError, match="database_write_failed"),
        AutonomousMemoryStore(path).writer(),
    ):
        pass
