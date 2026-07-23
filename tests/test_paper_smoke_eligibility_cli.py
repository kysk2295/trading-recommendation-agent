from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path

from tests.paper_smoke_eligibility_cli import (
    REPORT_NAME,
    run_eligibility_cli,
    run_isolated_eligibility_cli,
)
from tests.test_hermes_arm_authority_cli import ACCOUNT, SCOPE, _fixture
from trading_agent.execution_store import ExecutionStore
from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.paper_execution_models import AccountFingerprint


def test_cli_blocks_without_paper_champion_and_redacts_local_authority(
    tmp_path: Path,
) -> None:
    # Given
    fixture = _fixture(tmp_path)
    experiment_ledger = tmp_path / "state/no-champion.sqlite3"
    with ExperimentLedgerStore(experiment_ledger).writer():
        pass
    execution_database = tmp_path / "state/execution.sqlite3"
    with ExecutionStore(execution_database).writer() as writer:
        _ = writer.bind_account(
            AccountFingerprint(ACCOUNT),
            dt.datetime(2026, 7, 22, 14, 0, tzinfo=dt.UTC),
        )
    output_dir = tmp_path / "report"

    # When
    completed = run_eligibility_cli(
        "--session-id",
        SCOPE.session_id,
        "--repository",
        str(fixture.repository),
        "--lane-registry",
        str(fixture.lane_registry),
        "--experiment-ledger",
        str(experiment_ledger),
        "--execution-database",
        str(execution_database),
        "--output-dir",
        str(output_dir),
    )

    # Then
    assert completed.returncode == 1
    report_path = output_dir / REPORT_NAME
    report = report_path.read_text(encoding="utf-8")
    assert "- result: blocked" in report
    assert "- blocker: champion_missing" in report
    assert "- external provider/account/order mutation: 0" in report
    assert ACCOUNT not in report
    assert fixture.commit_sha not in report
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600


def test_cli_blocks_when_execution_store_is_uninitialized(tmp_path: Path) -> None:
    # Given
    fixture = _fixture(tmp_path)
    output_dir = tmp_path / "report"

    # When
    completed = run_eligibility_cli(
        "--session-id",
        SCOPE.session_id,
        "--repository",
        str(fixture.repository),
        "--lane-registry",
        str(fixture.lane_registry),
        "--experiment-ledger",
        str(fixture.experiment_ledger),
        "--execution-database",
        str(tmp_path / "state/missing-execution.sqlite3"),
        "--output-dir",
        str(output_dir),
    )

    # Then
    assert completed.returncode == 1
    report = (output_dir / REPORT_NAME).read_text(encoding="utf-8")
    assert "- blocker: uninitialized_execution_store" in report


def test_cli_blocks_when_execution_account_binding_differs(tmp_path: Path) -> None:
    # Given
    fixture = _fixture(tmp_path)
    execution_database = tmp_path / "state/execution.sqlite3"
    other_account = "f" * 64
    with ExecutionStore(execution_database).writer() as writer:
        _ = writer.bind_account(
            AccountFingerprint(other_account),
            dt.datetime(2026, 7, 22, 14, 0, tzinfo=dt.UTC),
        )
    output_dir = tmp_path / "report"

    # When
    completed = run_eligibility_cli(
        "--session-id",
        SCOPE.session_id,
        "--repository",
        str(fixture.repository),
        "--lane-registry",
        str(fixture.lane_registry),
        "--experiment-ledger",
        str(fixture.experiment_ledger),
        "--execution-database",
        str(execution_database),
        "--output-dir",
        str(output_dir),
    )

    # Then
    assert completed.returncode == 1
    report = (output_dir / REPORT_NAME).read_text(encoding="utf-8")
    assert "- blocker: account_mismatch" in report
    assert ACCOUNT not in report
    assert other_account not in report


def test_cli_blocks_invalid_session_without_traceback(tmp_path: Path) -> None:
    # Given
    fixture = _fixture(tmp_path)
    output_dir = tmp_path / "report"

    # When
    completed = run_eligibility_cli(
        "--session-id",
        "invalid-session",
        "--repository",
        str(fixture.repository),
        "--lane-registry",
        str(fixture.lane_registry),
        "--experiment-ledger",
        str(fixture.experiment_ledger),
        "--execution-database",
        str(tmp_path / "state/execution.sqlite3"),
        "--output-dir",
        str(output_dir),
    )

    # Then
    assert completed.returncode == 1
    assert completed.stderr == ""
    report = (output_dir / REPORT_NAME).read_text(encoding="utf-8")
    assert "- blocker: invalid_request" in report


def test_cli_reports_ready_to_request_arm_for_clean_exact_control_plane(
    tmp_path: Path,
) -> None:
    # Given
    fixture = _fixture(tmp_path)
    execution_database = tmp_path / "state/execution.sqlite3"
    with ExecutionStore(execution_database).writer() as writer:
        _ = writer.bind_account(
            AccountFingerprint(ACCOUNT),
            dt.datetime(2026, 7, 22, 14, 0, tzinfo=dt.UTC),
        )
    output_dir = tmp_path / "report"

    # When
    completed = run_eligibility_cli(
        "--session-id",
        SCOPE.session_id,
        "--repository",
        str(fixture.repository),
        "--lane-registry",
        str(fixture.lane_registry),
        "--experiment-ledger",
        str(fixture.experiment_ledger),
        "--execution-database",
        str(execution_database),
        "--output-dir",
        str(output_dir),
    )

    # Then
    assert completed.returncode == 0
    report = (output_dir / REPORT_NAME).read_text(encoding="utf-8")
    assert "- result: ready_to_request_arm" in report
    assert "- blocker:" not in report
    assert "- explicit arm still required: yes" in report
    assert "- external provider/account/order mutation: 0" in report


def test_isolated_cli_help_loads_declared_runtime_dependencies() -> None:
    # Given / When
    completed = run_isolated_eligibility_cli("--help")

    # Then
    assert completed.returncode == 0
    assert "--execution-database" in completed.stdout
    assert "Traceback" not in completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
