from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from tests.paper_smoke_eligibility_cli import REPORT_NAME, run_eligibility_cli
from tests.test_execution_store import _intent
from tests.test_hermes_arm_authority_cli import ACCOUNT, SCOPE, _fixture
from trading_agent.alpaca_paper_order_stream import (
    PaperTradeUpdateFrame,
    PaperTradeUpdateWireKind,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.trade_update_receipts import TradeUpdateReceiptReason


def test_cli_blocks_when_execution_intent_is_unresolved(tmp_path: Path) -> None:
    # Given
    fixture = _fixture(tmp_path)
    execution_database = tmp_path / "state/execution.sqlite3"
    with ExecutionStore(execution_database).writer() as writer:
        _ = writer.bind_account(
            AccountFingerprint(ACCOUNT),
            dt.datetime(2026, 7, 22, 14, 0, tzinfo=dt.UTC),
        )
        _ = writer.save_intent(_intent(), quantity=1)
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
    assert "- blocker: unresolved_execution_intents" in report
    assert _intent().intent_id not in report


def test_cli_blocks_when_trade_update_receipt_is_pending(tmp_path: Path) -> None:
    # Given
    fixture = _fixture(tmp_path)
    execution_database = tmp_path / "state/execution.sqlite3"
    with ExecutionStore(execution_database).writer() as writer:
        _ = writer.bind_account(
            AccountFingerprint(ACCOUNT),
            dt.datetime(2026, 7, 22, 14, 0, tzinfo=dt.UTC),
        )
        _ = writer.save_trade_update_receipt(
            PaperTradeUpdateFrame(
                payload=b'{"stream":"unclassified"}',
                wire_kind=PaperTradeUpdateWireKind.TEXT,
            ),
            account_fingerprint=AccountFingerprint(ACCOUNT),
            connection_epoch="eligibility-fixture",
            received_at=dt.datetime(2026, 7, 22, 14, 1, tzinfo=dt.UTC),
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
    assert "- blocker: pending_trade_update_receipts" in report
    assert "eligibility-fixture" not in report


def test_cli_blocks_when_trade_update_quarantine_is_unrecovered(
    tmp_path: Path,
) -> None:
    # Given
    fixture = _fixture(tmp_path)
    execution_database = tmp_path / "state/execution.sqlite3"
    observed_at = dt.datetime(2026, 7, 22, 14, 1, tzinfo=dt.UTC)
    with ExecutionStore(execution_database).writer() as writer:
        _ = writer.bind_account(
            AccountFingerprint(ACCOUNT),
            dt.datetime(2026, 7, 22, 14, 0, tzinfo=dt.UTC),
        )
        receipt = writer.save_trade_update_receipt(
            PaperTradeUpdateFrame(
                payload=b"not-json",
                wire_kind=PaperTradeUpdateWireKind.BINARY,
            ),
            account_fingerprint=AccountFingerprint(ACCOUNT),
            connection_epoch="quarantine-fixture",
            received_at=observed_at,
        )
        _ = writer.quarantine_trade_update_receipt(
            receipt.receipt_key,
            reason=TradeUpdateReceiptReason.PROTOCOL_ERROR,
            classified_at=observed_at + dt.timedelta(seconds=1),
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
    assert "- blocker: unrecovered_trade_update_quarantine" in report
    assert "quarantine-fixture" not in report


def test_cli_blocks_invalid_execution_schema_without_traceback(
    tmp_path: Path,
) -> None:
    # Given
    fixture = _fixture(tmp_path)
    execution_database = tmp_path / "state/invalid-execution.sqlite3"
    with sqlite3.connect(execution_database) as connection:
        _ = connection.execute("PRAGMA user_version = 9")
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
    assert completed.stderr == ""
    report = (output_dir / REPORT_NAME).read_text(encoding="utf-8")
    assert "- blocker: invalid_execution_store" in report
