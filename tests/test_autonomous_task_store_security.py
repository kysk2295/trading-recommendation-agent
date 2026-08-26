from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
from pathlib import Path

import pytest

from tests.test_autonomous_task_store import step_fixture, task_fixture, task_for
from trading_agent._autonomous_task_store_sqlite import _reader_connection
from trading_agent.autonomous_task_models import autonomous_step_payload
from trading_agent.autonomous_task_store import AutonomousTaskStore, InvalidAutonomousTaskStoreError
from trading_agent.research_agent_cycle_models import EvidenceId


def test_database_file_mode_is_private(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"

    # When
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task_fixture())

    # Then
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_reader_rejects_symlink_database_path(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    task = task_fixture()
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task)
    alias = tmp_path / "alias.sqlite3"
    alias.symlink_to(path)

    # When / Then
    with pytest.raises(InvalidAutonomousTaskStoreError, match="database_path_invalid"):
        AutonomousTaskStore(alias).reader().task(task.task_id)


def test_reader_connection_is_query_only(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task_fixture())

    # When / Then
    with _reader_connection(path) as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("INSERT INTO autonomous_tasks VALUES ('x','x','x','x')")


def test_sql_triggers_reject_task_delete_and_step_update(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    task = task_fixture()
    step = step_fixture(task)
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task)
        assert writer.append_step(step)

    # When / Then
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM autonomous_tasks WHERE task_id=?", (task.task_id,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE autonomous_task_steps SET sequence=2 WHERE step_id=?", (step.step_id,))


def test_reader_rejects_exact_schema_definition_tampering(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    task = task_fixture()
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER autonomous_tasks_no_update")
        connection.execute(
            "CREATE TRIGGER autonomous_tasks_no_update BEFORE UPDATE ON autonomous_tasks "
            "BEGIN SELECT RAISE(ABORT, 'different'); END"
        )

    # When / Then
    with pytest.raises(InvalidAutonomousTaskStoreError, match="schema_objects_invalid"):
        AutonomousTaskStore(path).reader().task(task.task_id)


def test_reader_rejects_corrupt_task_payload(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task_fixture())
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO autonomous_tasks VALUES ('x','x','x','{}')")

    # When / Then
    with pytest.raises(InvalidAutonomousTaskStoreError, match="task_payload_invalid"):
        AutonomousTaskStore(path).reader().tasks()


def test_second_writer_lease_is_rejected(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task_fixture())

        # When / Then
        with (
            pytest.raises(InvalidAutonomousTaskStoreError, match="database_write_failed"),
            AutonomousTaskStore(path).writer(),
        ):
            pass


def test_reader_rejects_valid_task_payload_with_wrong_stored_sha(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    task = task_for(EvidenceId(hashlib.sha256(b"wrong-sha-task").hexdigest()))
    payload = json.dumps(task.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task_fixture())
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO autonomous_tasks VALUES (?,?,?,?)",
            (task.task_id, task.root_source_evidence_id, "0" * 64, payload),
        )

    # When / Then
    with pytest.raises(InvalidAutonomousTaskStoreError, match="task_payload_invalid"):
        AutonomousTaskStore(path).reader().task(task.task_id)


def test_reader_rejects_valid_step_payload_with_wrong_stored_sha(tmp_path: Path) -> None:
    # Given
    path = tmp_path / "autonomous.sqlite3"
    task = task_fixture()
    step = step_fixture(task)
    payload = autonomous_step_payload(step)
    with AutonomousTaskStore(path).writer() as writer:
        assert writer.create_task(task)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO autonomous_task_steps VALUES (?,?,?,?,?,?)",
            (step.step_id, step.task_id, step.sequence, step.occurred_at.isoformat(), "0" * 64, payload),
        )

    # When / Then
    with pytest.raises(InvalidAutonomousTaskStoreError, match="step_payload_invalid"):
        AutonomousTaskStore(path).reader().steps(task.task_id)
