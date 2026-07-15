from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

import pytest

import run_alpaca_paper_entry_smoke as smoke_cli
import run_alpaca_paper_safety_mutation_smoke as safety_smoke_cli
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.lane_defaults import INTRADAY_PILOT_PAPER_RISK_CONFIG
from trading_agent.paper_execution_models import AccountFingerprint, BrokerOrderId
from trading_agent.paper_mutation_executor_models import (
    PaperMutationExecutionResult,
    PaperMutationExecutionState,
)
from trading_agent.paper_operating_mutation_models import PaperEntryMutationExecution
from trading_agent.paper_operating_session_models import PaperOperatingSession
from trading_agent.paper_order_gate_models import ApprovedPaperOrderGateDecision
from trading_agent.paper_risk import PaperSizingContext, size_paper_order

FINGERPRINT = AccountFingerprint("a" * 64)
NOW = dt.datetime(2026, 7, 14, 13, 36, 4, tzinfo=dt.UTC)
PROJECT = Path(__file__).parents[1]
ENTRY_SCRIPT = PROJECT / "run_alpaca_paper_entry_smoke.py"
_UV = shutil.which("uv")
assert _UV is not None
UV = Path(_UV)


def test_entry_smoke_is_executable_and_help_does_not_load_credentials() -> None:
    assert os.access(ENTRY_SCRIPT, os.X_OK)
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"

    completed = subprocess.run(
        (str(ENTRY_SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--arm-paper-mutation" in completed.stdout
    assert "--database" in completed.stdout
    assert "--intent-id" in completed.stdout


def test_paper_smoke_clis_share_the_intraday_lane_risk_contract() -> None:
    assert smoke_cli.SMOKE_RISK_CONFIG is INTRADAY_PILOT_PAPER_RISK_CONFIG
    assert safety_smoke_cli.SMOKE_RISK_CONFIG is INTRADAY_PILOT_PAPER_RISK_CONFIG


class FakeSession:
    def __init__(self) -> None:
        self.requests = []

    def execute_entry(self, request, arm):
        self.requests.append((request, arm))
        sized = size_paper_order(
            request.candidate_intent,
            PaperSizingContext(30000.0, request.liquidity_allowed_quantity, request.estimated_spread_bps),
            request.config,
        )
        assert sized is not None
        return PaperEntryMutationExecution(
            ApprovedPaperOrderGateDecision(sized),
            PaperMutationExecutionResult(
                "f" * 64,
                PaperMutationExecutionState.ACKNOWLEDGED,
                BrokerOrderId("entry-1"),
            ),
            (),
            NOW,
        )


def _arguments(database: Path, output: Path) -> list[str]:
    return [
        "--arm-paper-mutation",
        "ARM_ALPACA_PAPER_ONLY",
        "--database",
        str(database),
        "--output-dir",
        str(output),
        "--intent-id",
        "orb-AAPL-20260714-093600",
        "--symbol",
        "AAPL",
        "--entry-limit",
        "10",
        "--stop",
        "9.75",
        "--target-1r",
        "10.25",
        "--target-2r",
        "10.50",
        "--created-at",
        "2026-07-14T09:36:02-04:00",
        "--bar-start",
        "2026-07-14T09:35:00-04:00",
        "--bar-first-observed",
        "2026-07-14T09:36:01-04:00",
        "--liquidity-quantity",
        "100",
        "--spread-bps",
        "20",
    ]


def test_armed_smoke_uses_maximum_100_dollar_config_and_writes_report(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    with ExecutionStore(database).writer() as writer:
        _ = writer.bind_account(FINGERPRINT, NOW)
    session = FakeSession()

    @contextmanager
    def opener(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> Iterator[PaperOperatingSession]:
        yield session

    code = smoke_cli.main(
        _arguments(database, output),
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=opener,
    )

    report = (output / "paper_entry_smoke_ko.md").read_text(encoding="utf-8")
    assert code == 0
    assert session.requests[0][0].config.max_notional_dollars == 100.0
    assert session.requests[0][0].config.max_risk_dollars == 10.0
    assert "결과: acknowledged" in report
    assert "secret" not in report


def test_wrong_arm_fails_in_parser_before_credentials(tmp_path: Path) -> None:
    called = False

    def credentials() -> AlpacaPaperCredentials:
        nonlocal called
        called = True
        return AlpacaPaperCredentials("key", "secret")

    args = _arguments(tmp_path / "db", tmp_path / "out")
    args[1] = "WRONG"
    with pytest.raises(SystemExit) as captured:
        _ = smoke_cli.main(args, credential_loader=credentials)

    assert captured.value.code == 2
    assert called is False


def test_entry_smoke_redacts_runtime_error_details(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    with ExecutionStore(database).writer() as writer:
        _ = writer.bind_account(FINGERPRINT, NOW)

    def fail_open(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> AbstractContextManager[PaperOperatingSession]:
        raise OSError("sensitive-account-and-broker-id")

    code = smoke_cli.main(
        _arguments(database, output),
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=fail_open,
    )

    report = (output / "paper_entry_smoke_ko.md").read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert code == 2
    assert "안전 오류 유형: OSError" in report
    assert "안전 오류 유형: OSError" in captured.err
    assert "sensitive-account-and-broker-id" not in report
    assert "sensitive-account-and-broker-id" not in captured.err
