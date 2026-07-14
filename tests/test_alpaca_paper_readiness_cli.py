from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import run_alpaca_paper_readiness as readiness_cli
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    PaperOrderStreamHeartbeat,
    PaperStreamEpoch,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperMarketClockSnapshot,
)
from trading_agent.paper_order_gate_models import CompletePaperPortfolio
from trading_agent.paper_reconciliation import ReconciliationResult
from trading_agent.paper_runtime_session import (
    PaperLedgerReader,
    PaperRuntimeReadiness,
)

FINGERPRINT = AccountFingerprint("a" * 64)


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")


def _readiness() -> PaperRuntimeReadiness:
    observed_at = dt.datetime(2026, 7, 14, 13, 36, 5, tzinfo=dt.UTC)
    account = PaperAccountSnapshot(
        observed_at=observed_at,
        status="ACTIVE",
        trading_blocked=False,
        equity=Decimal("30000"),
        last_equity=Decimal("30000"),
        buying_power=Decimal("60000"),
        account_fingerprint=FINGERPRINT,
    )
    return PaperRuntimeReadiness(
        broker_state=PaperBrokerState(account, (), ()),
        market_clock=PaperMarketClockSnapshot(
            observed_at=observed_at,
            market_timestamp=observed_at,
            is_open=False,
            next_open=dt.datetime(2026, 7, 15, 13, 30, tzinfo=dt.UTC),
            next_close=dt.datetime(2026, 7, 15, 20, 0, tzinfo=dt.UTC),
        ),
        stream_heartbeat=PaperOrderStreamHeartbeat(
            connection_epoch=PaperStreamEpoch("epoch-1"),
            authorized_at=observed_at - dt.timedelta(seconds=5),
            subscribed_at=observed_at - dt.timedelta(seconds=5),
            pong_at=observed_at,
        ),
        reconciliation=ReconciliationResult(True, ()),
        portfolio=CompletePaperPortfolio(
            observed_at=account.observed_at,
            account_status=account.status,
            trading_blocked=account.trading_blocked,
            equity=account.equity,
            last_equity=account.last_equity,
            buying_power=account.buying_power,
            exposures=(),
        ),
    )


def _initialize(database: Path) -> None:
    with ExecutionStore(database).writer() as writer:
        _ = writer.bind_account(
            FINGERPRINT,
            dt.datetime(2026, 7, 14, 13, 25, tzinfo=dt.UTC),
        )


def test_readiness_confirms_stream_and_rest_without_claiming_order_approval(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    _initialize(database)

    code = readiness_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        probe_loader=lambda _credentials, _store: _readiness(),
    )

    report = (output / "paper_runtime_readiness_ko.md").read_text(
        encoding="utf-8"
    )
    assert code == 0
    assert "확인 시각: 2026-07-14T13:36:05+00:00" in report
    assert "주문 스트림: 인증·구독·Pong 확인" in report
    assert "활성 스트림 내부 REST·원장·포트폴리오 대사: 통과" in report
    assert "브로커 시장 개장: 아니오" in report
    assert "신규 주문 승인: 미평가" in report
    assert "POST/DELETE: 비활성" in report
    assert "test-key" not in report
    assert "test-secret" not in report
    assert FINGERPRINT not in report
    assert "epoch-1" not in report


def test_readiness_missing_ledger_fails_before_credentials_or_network(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing/execution.sqlite3"
    loader_called = False

    def loader(
        _: AlpacaPaperCredentials,
        _store: PaperLedgerReader,
    ) -> PaperRuntimeReadiness:
        nonlocal loader_called
        loader_called = True
        return _readiness()

    code = readiness_cli.main(
        [
            "--database",
            str(database),
            "--output-dir",
            str(tmp_path / "report"),
        ],
        credential_loader=_credentials,
        probe_loader=loader,
    )

    assert code == 1
    assert loader_called is False
    assert not database.exists()
    assert "초기화되지 않았습니다" in (
        tmp_path / "report/paper_runtime_readiness_ko.md"
    ).read_text(encoding="utf-8")


def test_readiness_rejects_a_different_paper_account(tmp_path: Path) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    _initialize(database)
    switched_account = replace(
        _readiness().broker_state.account,
        account_fingerprint=AccountFingerprint("b" * 64),
    )
    switched = replace(
        _readiness(),
        broker_state=replace(
            _readiness().broker_state,
            account=switched_account,
        ),
        reconciliation=ReconciliationResult(
            False,
            ("Alpaca paper 계좌 fingerprint가 실행 원장과 다릅니다",),
        ),
    )

    code = readiness_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        probe_loader=lambda _credentials, _store: switched,
    )

    assert code == 1
    assert "활성 스트림 내부 REST·원장·포트폴리오 대사: 차단" in (
        output / "paper_runtime_readiness_ko.md"
    ).read_text(encoding="utf-8")
