from __future__ import annotations

from pathlib import Path

import pytest

import run_alpaca_paper_recovery as recovery_cli
from trading_agent.alpaca_paper_activities import PaperActivityHistoryIncompleteError
from trading_agent.alpaca_paper_client import PaperOrderHistoryIncompleteError
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_trade_update_runtime import PaperTradeUpdateRecoveryProbe


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")


def test_recovery_cli_writes_only_sanitized_read_only_evidence(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    with ExecutionStore(database).writer():
        pass

    code = recovery_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        probe_loader=lambda _credentials, _store: PaperTradeUpdateRecoveryProbe(
            "2026-07-14T13:36:02+00:00",
            2,
            False,
            recovery_activity_count=3,
            recovery_protective_oco_count=1,
        ),
    )

    report = (output / "paper_stream_recovery_ko.md").read_text(encoding="utf-8")
    assert code == 0
    assert "정규화 주문 snapshot: 2건" in report
    assert "보호 OCO snapshot: 1건" in report
    assert "Account Activities FILL: 3건" in report
    assert "개별 execution 상세: 불완전" in report
    assert "WSS + REST GET only" in report
    assert "POST/PATCH/DELETE: 비활성" in report
    assert "test-key" not in report
    assert "test-secret" not in report


def test_recovery_cli_missing_ledger_fails_before_credentials_or_network(
    tmp_path: Path,
) -> None:
    database = tmp_path / "missing/execution.sqlite3"
    loader_called = False

    def load_credentials() -> AlpacaPaperCredentials:
        nonlocal loader_called
        loader_called = True
        return _credentials()

    code = recovery_cli.main(
        [
            "--database",
            str(database),
            "--output-dir",
            str(tmp_path / "report"),
        ],
        credential_loader=load_credentials,
    )

    assert code == 1
    assert loader_called is False
    assert not database.exists()
    assert "초기화되지 않았습니다" in (tmp_path / "report/paper_stream_recovery_ko.md").read_text(encoding="utf-8")


def test_recovery_cli_reports_incomplete_order_history_without_traceback(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    with ExecutionStore(database).writer():
        pass

    def fail_probe(
        _credentials: AlpacaPaperCredentials,
        _store: ExecutionStore,
    ) -> PaperTradeUpdateRecoveryProbe:
        raise PaperOrderHistoryIncompleteError

    code = recovery_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        probe_loader=fail_probe,
    )

    report = (output / "paper_stream_recovery_ko.md").read_text(encoding="utf-8")
    assert code == 2
    assert "안전 오류 유형: PaperOrderHistoryIncompleteError" in report


def test_recovery_cli_reports_persisted_snapshot_but_blocks_admission(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    with ExecutionStore(database).writer():
        pass

    code = recovery_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        probe_loader=lambda _credentials, _store: PaperTradeUpdateRecoveryProbe(
            "2026-07-14T13:36:02+00:00",
            1,
            True,
            ("immutable execution 충돌이 남아 있습니다",),
        ),
    )

    report = (output / "paper_stream_recovery_ko.md").read_text(encoding="utf-8")
    assert code == 1
    assert "immutable execution 충돌" in report
    assert "snapshot 저장: 완료" in report
    assert "신규 주문 admission: 차단" in report


def test_recovery_cli_reports_incomplete_activity_history_without_traceback(
    tmp_path: Path,
) -> None:
    # Given: an initialized ledger whose Account Activities pagination is incomplete.
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    with ExecutionStore(database).writer():
        pass

    def fail_probe(
        _credentials: AlpacaPaperCredentials,
        _store: ExecutionStore,
    ) -> PaperTradeUpdateRecoveryProbe:
        raise PaperActivityHistoryIncompleteError

    # When: the read-only recovery CLI reaches the boundary error.
    code = recovery_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        probe_loader=fail_probe,
    )

    # Then: it writes a sanitized failure report and returns the boundary exit code.
    report = (output / "paper_stream_recovery_ko.md").read_text(encoding="utf-8")
    assert code == 2
    assert "안전 오류 유형: PaperActivityHistoryIncompleteError" in report


def test_recovery_cli_redacts_runtime_error_details(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    with ExecutionStore(database).writer():
        pass

    def fail_probe(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> PaperTradeUpdateRecoveryProbe:
        raise OSError("sensitive-account-and-broker-id")

    code = recovery_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        probe_loader=fail_probe,
    )

    report = (output / "paper_stream_recovery_ko.md").read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert code == 2
    assert "안전 오류 유형: OSError" in report
    assert "안전 오류 유형: OSError" in captured.err
    assert "sensitive-account-and-broker-id" not in report
    assert "sensitive-account-and-broker-id" not in captured.err
