from __future__ import annotations

import datetime as dt
import sqlite3
from decimal import Decimal
from pathlib import Path

import run_alpaca_paper_safety as safety_cli
from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_database import _schema_through
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import BrokerOrderId, PaperOrderSide
from trading_agent.paper_safety_models import (
    BlockedPaperSafetyPlan,
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")


def _kill_plan() -> PaperSafetyPlan:
    return PaperSafetyPlan(
        FINGERPRINT,
        OBSERVED_AT,
        dt.date(2026, 7, 14),
        PaperSafetyPhase.KILL_SWITCH,
        Decimal("-226"),
        Decimal("-301"),
        (
            PaperCancelOrderAction(BrokerOrderId("entry-1"), "AAA", False),
            PaperClosePositionAction("AAA", PaperOrderSide.SELL, Decimal(10)),
        ),
    )


def test_safety_cli_writes_sanitized_get_only_plan_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    with ExecutionStore(database).writer():
        pass

    code = safety_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        plan_loader=lambda _credentials, _store: _kill_plan(),
    )

    report = (output / "paper_safety_plan_ko.md").read_text(encoding="utf-8")
    assert code == 0
    assert "kill switch" in report
    assert "보수적 일손익: -301 USD" in report
    assert "AAA: 신규진입 주문 취소" in report
    assert "AAA: sell 10주 평탄화" in report
    assert "WSS + REST GET only" in report
    assert "POST/PATCH/DELETE: 비활성" in report
    assert "entry-1" not in report
    assert "test-key" not in report
    assert "test-secret" not in report


def test_safety_cli_reports_fail_closed_decision(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    with ExecutionStore(database).writer():
        pass

    code = safety_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        plan_loader=lambda _credentials, _store: BlockedPaperSafetyPlan(("브로커 시장이 닫혀 있습니다",)),
    )

    report = (output / "paper_safety_plan_ko.md").read_text(encoding="utf-8")
    assert code == 1
    assert "안전조치 계획: 차단" in report
    assert "브로커 시장이 닫혀 있습니다" in report


def test_safety_cli_missing_ledger_fails_before_credentials(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing/execution.sqlite3"
    credential_called = False

    def credentials() -> AlpacaPaperCredentials:
        nonlocal credential_called
        credential_called = True
        return _credentials()

    code = safety_cli.main(
        [
            "--database",
            str(database),
            "--output-dir",
            str(tmp_path / "report"),
        ],
        credential_loader=credentials,
    )

    assert code == 1
    assert credential_called is False
    assert not database.exists()


def test_safety_cli_migrates_existing_v5_ledger_to_current_schema_before_network(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(f"{_schema_through(5)}\nPRAGMA user_version = 5;")

    code = safety_cli.main(
        [
            "--database",
            str(database),
            "--output-dir",
            str(tmp_path / "report"),
        ],
        credential_loader=_credentials,
        plan_loader=lambda _credentials, _store: _kill_plan(),
    )

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
    assert code == 0
    assert version == (7,)
