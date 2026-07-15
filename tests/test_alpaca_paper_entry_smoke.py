from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import cast

import pytest

import run_alpaca_paper_entry_smoke as smoke_cli
import run_alpaca_paper_safety_mutation_smoke as safety_smoke_cli
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_store import ExecutionStore
from trading_agent.lane_defaults import INTRADAY_PILOT_PAPER_RISK_CONFIG
from trading_agent.paper_entry_source import InvalidCurrentOrbPaperEntrySourceError
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    IntentId,
    PaperOrderIntent,
    PaperOrderSide,
)
from trading_agent.paper_mutation_executor_models import (
    PaperMutationExecutionResult,
    PaperMutationExecutionState,
)
from trading_agent.paper_mutation_keys import PaperMutationKey
from trading_agent.paper_operating_mutation_models import PaperEntryMutationExecution
from trading_agent.paper_operating_session_models import (
    PaperOperatingSession,
    PaperOrderAdmissionRequest,
)
from trading_agent.paper_order_gate_models import (
    ApprovedPaperOrderGateDecision,
    LatestCompletedBar,
)
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
    assert "--watch-database" in completed.stdout
    assert "--intent-id" not in completed.stdout


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
                PaperMutationKey("f" * 64),
                PaperMutationExecutionState.ACKNOWLEDGED,
                BrokerOrderId("entry-1"),
            ),
            (),
            NOW,
        )


def _request() -> PaperOrderAdmissionRequest:
    return PaperOrderAdmissionRequest(
        LatestCompletedBar(
            "AAPL",
            dt.datetime(2026, 7, 14, 13, 35, tzinfo=dt.UTC),
            dt.datetime(2026, 7, 14, 13, 36, 1, tzinfo=dt.UTC),
        ),
        PaperOrderIntent(
            IntentId("2026-07-14T09:36:02-04:00:AAPL:opening_range_breakout"),
            "orb",
            "paper-smoke-v1",
            "AAPL",
            dt.datetime(2026, 7, 14, 13, 36, 2, tzinfo=dt.UTC),
            PaperOrderSide.BUY,
            10.0,
            9.75,
            10.25,
            10.50,
        ),
        1,
        20.0,
        INTRADAY_PILOT_PAPER_RISK_CONFIG,
    )


def _arguments(database: Path, output: Path, watch_database: Path) -> list[str]:
    return [
        "--arm-paper-mutation",
        "ARM_ALPACA_PAPER_ONLY",
        "--database",
        str(database),
        "--output-dir",
        str(output),
        "--watch-database",
        str(watch_database),
    ]


def test_armed_smoke_uses_maximum_100_dollar_config_and_writes_report(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    watch_database = tmp_path / "watch.sqlite3"
    with ExecutionStore(database).writer() as writer:
        _ = writer.bind_account(FINGERPRINT, NOW)
    session = FakeSession()

    @contextmanager
    def opener(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> Iterator[PaperOperatingSession]:
        yield cast(PaperOperatingSession, session)

    code = smoke_cli.main(
        _arguments(database, output, watch_database),
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=opener,
        source_loader=lambda path, observed_at: (
            _request() if (path, observed_at) == (watch_database, NOW) else pytest.fail("unexpected source arguments")
        ),
        clock=lambda: NOW,
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

    args = _arguments(tmp_path / "db", tmp_path / "out", tmp_path / "watch")
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
    watch_database = tmp_path / "watch.sqlite3"
    with ExecutionStore(database).writer() as writer:
        _ = writer.bind_account(FINGERPRINT, NOW)

    def fail_open(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> AbstractContextManager[PaperOperatingSession]:
        raise OSError("sensitive-account-and-broker-id")

    code = smoke_cli.main(
        _arguments(database, output, watch_database),
        credential_loader=lambda: AlpacaPaperCredentials("key", "secret"),
        session_opener=fail_open,
        source_loader=lambda _path, _observed_at: _request(),
        clock=lambda: NOW,
    )

    report = (output / "paper_entry_smoke_ko.md").read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert code == 2
    assert "안전 오류 유형: OSError" in report
    assert "안전 오류 유형: OSError" in captured.err
    assert "sensitive-account-and-broker-id" not in report
    assert "sensitive-account-and-broker-id" not in captured.err


def test_old_free_form_candidate_options_are_rejected_before_source(
    tmp_path: Path,
) -> None:
    source_called = False

    def source_loader(
        _: Path,
        __: dt.datetime,
    ) -> PaperOrderAdmissionRequest:
        nonlocal source_called
        source_called = True
        return _request()

    args = _arguments(
        tmp_path / "execution.sqlite3",
        tmp_path / "report",
        tmp_path / "watch.sqlite3",
    )
    args.extend(("--intent-id", "forged-current-intent"))

    with pytest.raises(SystemExit) as captured:
        _ = smoke_cli.main(args, source_loader=source_loader, clock=lambda: NOW)

    assert captured.value.code == 2
    assert source_called is False


def test_source_rejection_happens_before_credentials_and_session(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "execution.sqlite3"
    output = tmp_path / "report"
    with ExecutionStore(database).writer() as writer:
        _ = writer.bind_account(FINGERPRINT, NOW)
    credential_called = False
    session_called = False

    def credentials() -> AlpacaPaperCredentials:
        nonlocal credential_called
        credential_called = True
        return AlpacaPaperCredentials("key", "secret")

    def open_session(
        _: AlpacaPaperCredentials,
        __: ExecutionStore,
    ) -> AbstractContextManager[PaperOperatingSession]:
        nonlocal session_called
        session_called = True
        raise AssertionError("session must not open")

    def reject_source(
        _: Path,
        __: dt.datetime,
    ) -> PaperOrderAdmissionRequest:
        raise InvalidCurrentOrbPaperEntrySourceError

    code = smoke_cli.main(
        _arguments(database, output, tmp_path / "sensitive-path/watch.sqlite3"),
        credential_loader=credentials,
        session_opener=open_session,
        source_loader=reject_source,
        clock=lambda: NOW,
    )

    report = (output / "paper_entry_smoke_ko.md").read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert code == 2
    assert credential_called is False
    assert session_called is False
    assert "안전 오류 유형: InvalidCurrentOrbPaperEntrySourceError" in report
    assert "sensitive-path" not in report
    assert "sensitive-path" not in captured.err
