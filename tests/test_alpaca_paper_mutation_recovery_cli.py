from __future__ import annotations

from pathlib import Path

import pytest

import run_alpaca_paper_mutation_recovery as recovery_cli
from tests.trade_update_ledger_fixtures import initialized_store
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import BrokerOrderId
from trading_agent.paper_mutation_keys import PaperMutationKey
from trading_agent.paper_mutation_recovery_models import (
    PaperMutationRecoveryResult,
    PaperMutationRecoveryState,
)


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", "test-secret")


def _results() -> tuple[PaperMutationRecoveryResult, ...]:
    return (
        PaperMutationRecoveryResult(
            PaperMutationKey("a" * 64),
            PaperMutationRecoveryState.ACKNOWLEDGED,
            BrokerOrderId("paper-order-secret-1"),
        ),
        PaperMutationRecoveryResult(
            PaperMutationKey("b" * 64),
            PaperMutationRecoveryState.ABSENT,
            None,
        ),
    )


def test_mutation_recovery_cli_writes_sanitized_get_only_evidence(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    output = tmp_path / "report"

    code = recovery_cli.main(
        ["--database", str(store.path), "--output-dir", str(output)],
        credential_loader=_credentials,
        recovery_loader=lambda _credentials, _store: _results(),
    )

    report = (output / "paper_mutation_recovery_ko.md").read_text(encoding="utf-8")
    assert code == 0
    assert "확인 완료: 1" in report
    assert "부재 확인: 1" in report
    assert "미해결: 0" in report
    assert "WSS + REST GET only" in report
    assert "POST/PATCH/DELETE: 비활성" in report
    assert "paper-order-secret-1" not in report
    assert "test-key" not in report
    assert "test-secret" not in report


def test_mutation_recovery_cli_returns_nonzero_for_unresolved(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    unresolved = PaperMutationRecoveryResult(
        PaperMutationKey("c" * 64),
        PaperMutationRecoveryState.UNRESOLVED,
        None,
    )

    code = recovery_cli.main(
        [
            "--database",
            str(store.path),
            "--output-dir",
            str(tmp_path / "report"),
        ],
        credential_loader=_credentials,
        recovery_loader=lambda _credentials, _store: (unresolved,),
    )

    assert code == 1


def test_mutation_recovery_cli_missing_ledger_fails_before_credentials(
    tmp_path: Path,
) -> None:
    credential_called = False

    def credentials() -> AlpacaPaperCredentials:
        nonlocal credential_called
        credential_called = True
        return _credentials()

    database = tmp_path / "missing/execution.sqlite3"
    code = recovery_cli.main(
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


def test_mutation_recovery_cli_redacts_runtime_error_details(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = initialized_store(tmp_path)
    output = tmp_path / "report"

    def fail_recovery(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> tuple[PaperMutationRecoveryResult, ...]:
        raise OSError("sensitive-account-and-broker-id")

    code = recovery_cli.main(
        ["--database", str(store.path), "--output-dir", str(output)],
        credential_loader=_credentials,
        recovery_loader=fail_recovery,
    )

    report = (output / "paper_mutation_recovery_ko.md").read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert code == 2
    assert "안전 오류 유형: OSError" in report
    assert "안전 오류 유형: OSError" in captured.err
    assert "sensitive-account-and-broker-id" not in report
    assert "sensitive-account-and-broker-id" not in captured.err
