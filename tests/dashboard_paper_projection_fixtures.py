from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path
from typing import Literal

from trading_agent.execution_store import ExecutionStore
from trading_agent.lane_contract_keys import experiment_scope_key, lane_manifest_key
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_defaults import CURRENT_INTRADAY_EXPERIMENT_SCOPES, INTRADAY_MANIFEST
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryStore
from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.paper_safety_models import PaperSafetyPhase, PaperSafetyPlan
from trading_agent.paper_stream_recovery_models import PaperStreamRecoveryObservation

FINALIZED_AT = dt.datetime(2026, 7, 25, 20, 5, tzinfo=dt.UTC)
FINGERPRINT = AccountFingerprint("b" * 64)

type MissingStage = Literal["reconcile", "cutoff", "eod_flat"]


@dataclass(frozen=True, slots=True)
class LifecycleFixture:
    missing: MissingStage | None = None
    reconcile_at: dt.datetime = dt.datetime(2026, 7, 25, 19, 40, tzinfo=dt.UTC)
    cutoff_at: dt.datetime = dt.datetime(2026, 7, 25, 19, 45, tzinfo=dt.UTC)
    eod_at: dt.datetime = dt.datetime(2026, 7, 25, 19, 50, tzinfo=dt.UTC)
    eod_session_date: dt.date = dt.date(2026, 7, 25)


COMPLETE_LIFECYCLE = LifecycleFixture()


def append_finalized_lifecycle(
    outputs: Path,
    fixture: LifecycleFixture = COMPLETE_LIFECYCLE,
) -> ExecutionStore:
    store = ExecutionStore(outputs / "paper" / "execution.sqlite3")
    with store.writer() as writer:
        assert writer.bind_account(
            FINGERPRINT,
            dt.datetime(2026, 7, 25, 13, 30, tzinfo=dt.UTC),
        )
        if fixture.missing != "reconcile":
            _ = writer.append_paper_stream_recovery(
                PaperStreamRecoveryObservation(
                    account_fingerprint=FINGERPRINT,
                    connection_epoch="dashboard-finalized",
                    started_at=fixture.reconcile_at - dt.timedelta(seconds=1),
                    completed_at=fixture.reconcile_at,
                    snapshot_json='{"orders":[],"positions":[]}',
                    execution_detail_complete=True,
                )
            )
        if fixture.missing != "cutoff":
            _ = writer.save_paper_safety_plan(safety_plan(PaperSafetyPhase.ENTRY_CUTOFF, fixture.cutoff_at))
        if fixture.missing != "eod_flat":
            _ = writer.save_paper_safety_plan(
                replace(
                    safety_plan(PaperSafetyPhase.EOD_FLATTEN, fixture.eod_at),
                    session_date=fixture.eod_session_date,
                )
            )
    identity = store.ledger_snapshot_identity()
    append_daily_snapshot(
        outputs,
        complete=True,
        source_generation=identity.generation,
        source_sha256=identity.sha256,
    )
    return store


def append_daily_snapshot(
    outputs: Path,
    *,
    complete: bool,
    source_generation: int = 42,
    source_sha256: str = "a" * 64,
) -> None:
    registry = LaneRegistryStore(outputs / "lane_control" / "lane_registry.sqlite3")
    scope = CURRENT_INTRADAY_EXPERIMENT_SCOPES[0]
    snapshot = finalized_snapshot(
        complete=complete,
        source_generation=source_generation,
        source_sha256=source_sha256,
    )
    with registry.writer() as writer:
        _ = writer.register_manifest(INTRADAY_MANIFEST)
        _ = writer.register_experiment_scope(scope)
        assert writer.append_daily_snapshot(snapshot)


def finalized_snapshot(
    *,
    complete: bool = True,
    source_generation: int = 42,
    source_sha256: str = "a" * 64,
) -> LaneDailySnapshot:
    scope = CURRENT_INTRADAY_EXPERIMENT_SCOPES[0]
    return LaneDailySnapshot(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=dt.date(2026, 7, 25),
        finalized_at=FINALIZED_AT,
        manifest_key=lane_manifest_key(INTRADAY_MANIFEST),
        experiment_scope_keys=(experiment_scope_key(scope),),
        source_ledger_generation=source_generation,
        source_ledger_sha256=source_sha256,
        champion_strategy_versions=(),
        data_quality_complete=complete,
        allocation_eligible=False,
        incidents=(),
        conservative_equity=Decimal("100125.25"),
        realized_pnl=Decimal("125.25"),
        unrealized_pnl=Decimal("-20.50"),
        planned_open_risk=Decimal("0"),
        open_order_count=0,
        open_position_count=0,
    )


def safety_plan(phase: PaperSafetyPhase, observed_at: dt.datetime) -> PaperSafetyPlan:
    return PaperSafetyPlan(
        account_fingerprint=FINGERPRINT,
        observed_at=observed_at,
        session_date=dt.date(2026, 7, 25),
        phase=phase,
        mark_to_market_daily_pnl=Decimal("104.75"),
        conservative_daily_pnl=Decimal("104.75"),
        actions=(),
    )
