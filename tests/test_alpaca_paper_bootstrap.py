from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import run_alpaca_paper_bootstrap as bootstrap_cli
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperPositionSnapshot,
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


def test_bootstrap_initializes_and_binds_empty_paper_account(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"

    # When
    code = bootstrap_cli.main(
        ["--database", str(database), "--output-dir", str(output)],
        credential_loader=_credentials,
        state_loader=_empty_state,
    )

    # Then
    store = ExecutionStore(database)
    report = (output / "paper_bootstrap_ko.md").read_text(encoding="utf-8")
    assert code == 0
    assert store.is_initialized() is True
    assert store.account_fingerprint() == FINGERPRINT
    assert "결합: 완료" in report
    assert FINGERPRINT not in report


def test_bootstrap_second_writer_fails_before_provider_read(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "execution.sqlite3"
    state_loader_called = False

    def state_loader(_: AlpacaPaperCredentials) -> PaperBrokerState:
        nonlocal state_loader_called
        state_loader_called = True
        return _empty_state(_credentials())

    # When
    with ExecutionStore(database).writer():
        code = bootstrap_cli.main(
            [
                "--database",
                str(database),
                "--output-dir",
                str(tmp_path / "report"),
            ],
            credential_loader=_credentials,
            state_loader=state_loader,
        )

    # Then
    assert code == 2
    assert state_loader_called is False


def test_bootstrap_refuses_account_with_open_position(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "execution.sqlite3"
    position = PaperPositionSnapshot("AAA", Decimal("0.5"), Decimal("5"))

    # When
    code = bootstrap_cli.main(
        [
            "--database",
            str(database),
            "--output-dir",
            str(tmp_path / "report"),
        ],
        credential_loader=_credentials,
        state_loader=lambda _: PaperBrokerState(_account(), (), (position,)),
    )

    # Then
    assert code == 1
    assert ExecutionStore(database).account_fingerprint() is None
