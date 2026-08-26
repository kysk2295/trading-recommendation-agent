from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.test_autonomous_task_store import step_fixture, task_fixture
from trading_agent import _autonomous_task_store_sqlite as store_sqlite
from trading_agent import autonomous_task_store as task_store_module
from trading_agent.autonomous_task_store import AutonomousTaskStore, InvalidAutonomousTaskStoreError


def test_atomic_initial_admission_flush_failure_leaves_no_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an initialized store whose next durable generation flush fails.
    path = tmp_path / "autonomous.sqlite3"
    store = AutonomousTaskStore(path)
    with store.writer():
        pass
    task = task_fixture()
    step = step_fixture(task)

    def fail_flush(identity: store_sqlite._DatabaseIdentity, connection: sqlite3.Connection) -> None:
        del identity, connection
        raise OSError

    monkeypatch.setattr(task_store_module, "_flush_writer_generation", fail_flush)

    # When: atomic root creation reaches the failed persistence boundary.
    with (
        pytest.raises(InvalidAutonomousTaskStoreError, match="writer_generation_flush_failed"),
        store.writer() as writer,
    ):
        writer.create_task_with_initial_step(task, step)

    # Then: neither half of the admission is visible after reconciliation.
    assert store.reader().task(task.task_id) is None
    assert store.reader().steps(task.task_id) == ()
