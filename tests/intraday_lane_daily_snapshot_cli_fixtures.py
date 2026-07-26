from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

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
from trading_agent.paper_safety_models import PaperSafetyPhase, PaperSafetyPlan
from trading_agent.paper_stream_recovery_models import PaperStreamRecoveryObservation
from trading_agent.strategy_factory import StrategyMode

SESSION_DATE = dt.date(2026, 7, 14)
SESSION_CLOSE = dt.datetime(2026, 7, 14, 20, tzinfo=dt.UTC)
FINALIZED_AT = dt.datetime(2026, 7, 15, 0, 5, tzinfo=dt.UTC)
OBSERVED_AT = FINALIZED_AT - dt.timedelta(seconds=1)
BOUND_AT = dt.datetime(2026, 7, 14, 13, 25, tzinfo=dt.UTC)
FINGERPRINT = AccountFingerprint("a" * 64)
SECRET = "test-secret"
REPORT_NAME = "intraday_lane_daily_snapshot_ko.md"


@dataclass(frozen=True, slots=True)
class Sources:
    registry: LaneRegistryStore
    execution: ExecutionStore
    session: Path


def sources(tmp_path: Path) -> Sources:
    outputs = tmp_path / "outputs"
    execution = ExecutionStore(outputs / "paper" / "execution.sqlite3")
    with execution.writer() as writer:
        assert writer.bind_account(FINGERPRINT, BOUND_AT) is True
        assert writer.append_paper_stream_recovery(
            PaperStreamRecoveryObservation(
                account_fingerprint=FINGERPRINT,
                connection_epoch="intraday-finalizer-e2e",
                started_at=SESSION_CLOSE - dt.timedelta(minutes=21),
                completed_at=SESSION_CLOSE - dt.timedelta(minutes=20),
                snapshot_json='{"orders":[],"positions":[]}',
                execution_detail_complete=True,
            )
        )
        assert writer.save_paper_safety_plan(
            safety_plan(PaperSafetyPhase.ENTRY_CUTOFF, SESSION_CLOSE - dt.timedelta(minutes=15))
        )
        assert writer.save_paper_safety_plan(
            safety_plan(PaperSafetyPhase.EOD_FLATTEN, SESSION_CLOSE - dt.timedelta(minutes=5))
        )

    registry = LaneRegistryStore(outputs / "lane_control" / "lane_registry.sqlite3")
    orb_scope = current_intraday_experiment_scope("H-MOM-ORB-001")
    with registry.writer() as writer:
        assert writer.register_manifest(INTRADAY_MANIFEST) is True
        assert writer.register_experiment_scope(orb_scope) is True
        assert writer.bind_account(
            lane_account_binding(
                INTRADAY_MANIFEST,
                FINGERPRINT,
                hashlib.sha256(str(execution.path).encode()).hexdigest(),
                BOUND_AT,
            )
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
    return Sources(registry, execution, session)


def safety_plan(
    phase: PaperSafetyPhase,
    observed_at: dt.datetime,
) -> PaperSafetyPlan:
    return PaperSafetyPlan(
        account_fingerprint=FINGERPRINT,
        observed_at=observed_at,
        session_date=SESSION_DATE,
        phase=phase,
        mark_to_market_daily_pnl=Decimal("0"),
        conservative_daily_pnl=Decimal("0"),
        actions=(),
    )


def flat_readiness() -> PaperRuntimeReadiness:
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


def credentials() -> AlpacaPaperCredentials:
    return AlpacaPaperCredentials("test-key", SECRET)


def args(
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


def report(output: Path) -> str:
    return (output / REPORT_NAME).read_text(encoding="utf-8")


def assert_redacted(value: str, fixture: Sources) -> None:
    assert str(FINGERPRINT) not in value
    assert "test-key" not in value
    assert SECRET not in value
    assert str(fixture.registry.path) not in value
    assert str(fixture.execution.path) not in value
    assert "manifest_key" not in value
    assert "scope_key" not in value
    assert "sha256" not in value
