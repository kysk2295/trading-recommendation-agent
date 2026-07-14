from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import run_alpaca_paper_preflight as preflight_cli
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    IntentId,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperOrderSide,
    PaperOrderSnapshot,
)

FINGERPRINT = AccountFingerprint("a" * 64)


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")


def _account() -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        observed_at=dt.datetime(2026, 7, 14, 13, 25, tzinfo=dt.UTC),
        status="ACTIVE",
        trading_blocked=False,
        account_fingerprint=FINGERPRINT,
    )


def _empty_state(_: AlpacaPaperCredentials) -> PaperBrokerState:
    return PaperBrokerState(_account(), (), ())


def _initialize(database: Path) -> None:
    with ExecutionStore(database).writer() as writer:
        _ = writer.bind_account(
            FINGERPRINT,
            dt.datetime(2026, 7, 14, 13, 25, tzinfo=dt.UTC),
        )


def test_preflight_writes_ready_report_for_bound_empty_account(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    _initialize(database)

    # When
    code = preflight_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        state_loader=_empty_state,
    )

    # Then
    report = (output / "paper_preflight_ko.md").read_text(encoding="utf-8")
    assert code == 0
    assert "준비: 예" in report
    assert "미체결 주문: 0" in report
    assert "열린 포지션: 0" in report
    assert "test-key" not in report
    assert "test-secret" not in report
    assert FINGERPRINT not in report


def test_preflight_missing_ledger_fails_without_creating_database(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "missing/execution.sqlite3"
    loader_called = False

    def reject_loader(_: AlpacaPaperCredentials) -> PaperBrokerState:
        nonlocal loader_called
        loader_called = True
        return _empty_state(_credentials())

    # When
    code = preflight_cli.main(
        [
            "--database",
            str(database),
            "--output-dir",
            str(tmp_path / "report"),
        ],
        credential_loader=_credentials,
        state_loader=reject_loader,
    )

    # Then
    assert code == 1
    assert loader_called is False
    assert not database.exists()
    assert not database.parent.exists()


def test_preflight_returns_one_for_unknown_order(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    _initialize(database)
    unknown = PaperOrderSnapshot(
        BrokerOrderId("paper-order-1"),
        IntentId("unknown-intent"),
        "AAA",
        PaperOrderSide.BUY,
        "accepted",
        Decimal("1"),
        Decimal("0"),
        Decimal("10"),
        "day",
        False,
    )

    # When
    code = preflight_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        state_loader=lambda _: PaperBrokerState(_account(), (unknown,), ()),
    )

    # Then
    assert code == 1
    assert "알 수 없는 paper 주문" in (
        output / "paper_preflight_ko.md"
    ).read_text(encoding="utf-8")
