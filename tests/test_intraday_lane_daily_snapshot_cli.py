from __future__ import annotations

import datetime as dt
import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import pytest

import run_intraday_lane_daily_snapshot as snapshot_cli
from tests.daily_research_fixtures import write_complete_session
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    PaperOrderStreamHeartbeat,
    PaperStreamEpoch,
)
from trading_agent.daily_research_ledger import build_daily_record, write_daily_record
from trading_agent.execution_store import ExecutionStore
from trading_agent.lane_contract_models import lane_account_binding
from trading_agent.lane_defaults import (
    INTRADAY_MANIFEST,
    current_intraday_experiment_scope,
)
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperMarketClockSnapshot,
)
from trading_agent.paper_order_gate_models import CompletePaperPortfolio
from trading_agent.paper_reconciliation import ReconciliationResult
from trading_agent.paper_runtime import PaperRuntimeReadiness
from trading_agent.strategy_factory import StrategyMode

PROJECT = Path(__file__).parents[1]
SCRIPT = PROJECT / "run_intraday_lane_daily_snapshot.py"
_UV = shutil.which("uv")
assert _UV is not None
UV = Path(_UV)

SESSION_DATE = dt.date(2026, 7, 14)
SESSION_CLOSE = dt.datetime(2026, 7, 14, 20, tzinfo=dt.UTC)
FINALIZED_AT = dt.datetime(2026, 7, 15, 0, 5, tzinfo=dt.UTC)
OBSERVED_AT = FINALIZED_AT - dt.timedelta(seconds=1)
BOUND_AT = dt.datetime(2026, 7, 14, 13, 25, tzinfo=dt.UTC)
FINGERPRINT = AccountFingerprint("a" * 64)
SECRET = "test-secret"
REPORT_NAME = "intraday_lane_daily_snapshot_ko.md"


@dataclass(frozen=True, slots=True)
class _Sources:
    registry: LaneRegistryStore
    execution: ExecutionStore
    session: Path


def test_snapshot_help_is_executable_without_fixture_bypass() -> None:
    completed = subprocess.run(
        (str(SCRIPT), "--help"),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 0, completed.stderr
    assert "--session-date" in completed.stdout
    assert "--execution-database" in completed.stdout
    assert "--lane-registry" in completed.stdout
    assert "fixture" not in completed.stdout.lower()


def test_snapshot_invalid_date_is_argparse_error() -> None:
    completed = subprocess.run(
        (
            str(SCRIPT),
            "missing-session",
            "--session-date",
            "not-a-date",
            "--execution-database",
            "missing-execution",
            "--lane-registry",
            "missing-registry",
            "--output-dir",
            "missing-output",
        ),
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
        env=_direct_execution_environment(),
    )

    assert completed.returncode == 2
    assert "YYYY-MM-DD" in completed.stderr


@pytest.mark.parametrize("missing", ("registry", "execution", "session"))
def test_missing_local_source_blocks_before_credentials(
    tmp_path: Path,
    missing: str,
) -> None:
    sources = _sources(tmp_path)
    output = tmp_path / f"report-{missing}"
    paths = {
        "registry": sources.registry.path,
        "execution": sources.execution.path,
        "session": sources.session,
    }
    missing_path = tmp_path / f"missing-{missing}"
    paths[missing] = missing_path
    credential_calls = 0

    def credential_loader() -> AlpacaPaperCredentials:
        nonlocal credential_calls
        credential_calls += 1
        return _credentials()

    code = snapshot_cli.main(
        _args(paths["session"], paths["execution"], paths["registry"], output),
        credential_loader=credential_loader,
        probe_loader=lambda _credentials, _store: _flat_readiness(),
        clock=lambda: FINALIZED_AT,
    )

    assert code == 1
    assert credential_calls == 0
    assert not missing_path.exists()
    report = _report(output)
    assert "결과: blocked" in report
    assert "snapshot append: not_written" in report
    _assert_redacted(report, sources)


def test_fake_flat_readiness_creates_then_replays_redacted_snapshot(
    tmp_path: Path,
) -> None:
    sources = _sources(tmp_path)
    first_output = tmp_path / "report-first"
    replay_output = tmp_path / "report-replay"
    probe_calls = 0

    def probe_loader(
        credentials: AlpacaPaperCredentials,
        store: object,
    ) -> PaperRuntimeReadiness:
        nonlocal probe_calls
        probe_calls += 1
        assert credentials.key_id == "test-key"
        assert credentials.secret_key == SECRET
        assert isinstance(store, ExecutionStore)
        assert store.path == sources.execution.path
        return _flat_readiness()

    first = snapshot_cli.main(
        _args(
            sources.session,
            sources.execution.path,
            sources.registry.path,
            first_output,
        ),
        credential_loader=_credentials,
        probe_loader=probe_loader,
        clock=lambda: FINALIZED_AT,
    )
    replay = snapshot_cli.main(
        _args(
            sources.session,
            sources.execution.path,
            sources.registry.path,
            replay_output,
        ),
        credential_loader=_credentials,
        probe_loader=probe_loader,
        clock=lambda: FINALIZED_AT,
    )

    assert first == 0
    assert replay == 0
    assert probe_calls == 2
    assert len(sources.registry.daily_snapshots()) == 1
    first_report = _report(first_output)
    replay_report = _report(replay_output)
    assert "결과: finalized" in first_report
    assert "snapshot append: created" in first_report
    assert "snapshot append: replayed" in replay_report
    assert "미체결 주문: 0" in first_report
    assert "열린 포지션: 0" in first_report
    assert "데이터 품질 완료: 예" in first_report
    assert "allocation eligible: 아니오" in first_report
    assert "외부 Alpaca mutation: 0건" in first_report
    _assert_redacted(first_report, sources)
    _assert_redacted(replay_report, sources)


def test_broker_blocked_readiness_writes_only_generic_blocked_report(
    tmp_path: Path,
) -> None:
    sources = _sources(tmp_path)
    output = tmp_path / "blocked-report"
    blocked = replace(
        _flat_readiness(),
        market_clock=replace(_flat_readiness().market_clock, is_open=True),
        runtime_reasons=("sensitive-upstream-detail",),
    )

    code = snapshot_cli.main(
        _args(
            sources.session,
            sources.execution.path,
            sources.registry.path,
            output,
        ),
        credential_loader=_credentials,
        probe_loader=lambda _credentials, _store: blocked,
        clock=lambda: FINALIZED_AT,
    )

    assert code == 1
    assert sources.registry.daily_snapshots() == ()
    report = _report(output)
    assert "결과: blocked" in report
    assert "snapshot append: not_written" in report
    assert "sensitive-upstream-detail" not in report
    assert "외부 Alpaca mutation: 0건" in report
    _assert_redacted(report, sources)


def _sources(tmp_path: Path) -> _Sources:
    execution = ExecutionStore(tmp_path / "execution.sqlite3")
    with execution.writer() as writer:
        assert writer.bind_account(FINGERPRINT, BOUND_AT) is True

    registry = LaneRegistryStore(tmp_path / "lane-registry.sqlite3")
    orb_scope = current_intraday_experiment_scope("H-MOM-ORB-001")
    with registry.writer() as writer:
        assert writer.register_manifest(INTRADAY_MANIFEST) is True
        assert writer.register_experiment_scope(orb_scope) is True
        assert (
            writer.bind_account(
                lane_account_binding(
                    INTRADAY_MANIFEST,
                    FINGERPRINT,
                    hashlib.sha256(str(execution.path).encode()).hexdigest(),
                    BOUND_AT,
                )
            )
            is True
        )

    session = tmp_path / "live_sessions" / "20260714"
    write_complete_session(session, SESSION_DATE)
    record = build_daily_record(
        session,
        SESSION_DATE,
        StrategyMode.ORB,
        "test-code",
        SESSION_CLOSE + dt.timedelta(minutes=2),
    )
    assert write_daily_record(session, record) is True
    return _Sources(registry, execution, session)


def _flat_readiness() -> PaperRuntimeReadiness:
    account = PaperAccountSnapshot(
        observed_at=OBSERVED_AT,
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
            observed_at=OBSERVED_AT,
            market_timestamp=OBSERVED_AT,
            is_open=False,
            next_open=dt.datetime(2026, 7, 15, 13, 30, tzinfo=dt.UTC),
            next_close=dt.datetime(2026, 7, 15, 20, tzinfo=dt.UTC),
        ),
        stream_heartbeat=PaperOrderStreamHeartbeat(
            connection_epoch=PaperStreamEpoch("test-epoch"),
            authorized_at=OBSERVED_AT - dt.timedelta(seconds=2),
            subscribed_at=OBSERVED_AT - dt.timedelta(seconds=1),
            pong_at=OBSERVED_AT,
        ),
        reconciliation=ReconciliationResult(True, ()),
        portfolio=CompletePaperPortfolio(
            observed_at=OBSERVED_AT,
            account_status=account.status,
            trading_blocked=account.trading_blocked,
            equity=account.equity,
            last_equity=account.last_equity,
            buying_power=account.buying_power,
            exposures=(),
        ),
    )


def _credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", SECRET)


def _args(
    session: Path,
    execution: Path,
    registry: Path,
    output: Path,
) -> list[str]:
    return [
        str(session),
        "--session-date",
        SESSION_DATE.isoformat(),
        "--execution-database",
        str(execution),
        "--lane-registry",
        str(registry),
        "--output-dir",
        str(output),
    ]


def _report(output: Path) -> str:
    return (output / REPORT_NAME).read_text(encoding="utf-8")


def _assert_redacted(report: str, sources: _Sources) -> None:
    assert str(FINGERPRINT) not in report
    assert "test-key" not in report
    assert SECRET not in report
    assert str(sources.registry.path) not in report
    assert str(sources.execution.path) not in report
    assert "manifest_key" not in report
    assert "scope_key" not in report
    assert "sha256" not in report


def _direct_execution_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PATH"] = f"{UV.parent}:/usr/bin:/bin"
    return environment
