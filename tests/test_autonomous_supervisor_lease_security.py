from __future__ import annotations

import os
from pathlib import Path

import pytest

import trading_agent._autonomous_supervisor_execution as execution
from tests.test_autonomous_task_models import task_fixture
from trading_agent._autonomous_supervisor_execution import AutonomousExecutionError, task_execution_lease


def _lease_path(database: Path) -> Path:
    task = task_fixture()
    return database.with_name(f".{database.name}.{task.task_id}.execution.lock")


def _acquire(database: Path) -> None:
    with task_execution_lease(database, task_fixture().task_id) as acquired:
        assert acquired


@pytest.mark.parametrize("attack", ("symlink", "permissions", "hardlink"))
def test_execution_lease_rejects_unsafe_leaf(tmp_path: Path, attack: str) -> None:
    database = tmp_path / "tasks.sqlite3"
    lease = _lease_path(database)
    target = tmp_path / "attacker"
    target.write_text("attacker", encoding="utf-8")
    if attack == "symlink":
        lease.symlink_to(target)
    else:
        lease.write_text("lease", encoding="utf-8")
        lease.chmod(0o644 if attack == "permissions" else 0o600)
        if attack == "hardlink":
            os.link(lease, tmp_path / "second-link")

    with pytest.raises(AutonomousExecutionError, match="autonomous_execution_lease"):
        _acquire(database)


def test_execution_lease_rejects_parent_path_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    database = parent / "tasks.sqlite3"
    displaced = tmp_path / "displaced"
    original = execution.require_open_directory_path
    swapped = False

    def swap(path: Path, descriptor: int) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            parent.rename(displaced)
            parent.mkdir(mode=0o700)
        original(path, descriptor)

    monkeypatch.setattr(execution, "require_open_directory_path", swap)

    with pytest.raises(AutonomousExecutionError, match="autonomous_execution_lease"):
        _acquire(database)
