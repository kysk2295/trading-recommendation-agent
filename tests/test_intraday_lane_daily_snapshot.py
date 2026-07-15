from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.daily_research_fixtures import write_complete_session
from trading_agent.alpaca_paper_order_stream import (
    PaperOrderStreamHeartbeat,
    PaperStreamEpoch,
)
from trading_agent.daily_research_ledger import build_daily_record, write_daily_record
from trading_agent.execution_store import ExecutionStore
from trading_agent.intraday_lane_daily_snapshot import (
    InvalidIntradayLaneFinalizationError,
    finalize_intraday_lane_day,
    preflight_intraday_lane_day,
)
from trading_agent.lane_contract_keys import experiment_scope_key, lane_manifest_key
from trading_agent.lane_contract_models import lane_account_binding
from trading_agent.lane_defaults import (
    INTRADAY_MANIFEST,
    current_intraday_experiment_scope,
)
from trading_agent.lane_registry_store import (
    LaneRegistryConflictError,
    LaneRegistryStore,
)
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerEventKey,
    BrokerOrderEvent,
    BrokerOrderEventType,
    BrokerOrderId,
    IntentId,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperMarketClockSnapshot,
    PaperOrderIntent,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_order_gate_models import CompletePaperPortfolio
from trading_agent.paper_reconciliation import ReconciliationResult
from trading_agent.paper_runtime import PaperRuntimeReadiness
from trading_agent.strategy_factory import StrategyMode

SESSION_DATE = dt.date(2026, 7, 14)
SESSION_CLOSE = dt.datetime(2026, 7, 14, 20, tzinfo=dt.UTC)
EVALUATED_AT = dt.datetime(2026, 7, 15, 0, 5, tzinfo=dt.UTC)
OBSERVED_AT = EVALUATED_AT - dt.timedelta(seconds=1)
BOUND_AT = dt.datetime(2026, 7, 14, 13, 25, tzinfo=dt.UTC)
FINGERPRINT = AccountFingerprint("a" * 64)
OTHER_FINGERPRINT = AccountFingerprint("b" * 64)
ORB_SCOPE = current_intraday_experiment_scope("H-MOM-ORB-001")
ORB_SCOPE_KEY = experiment_scope_key(ORB_SCOPE)


@dataclass(frozen=True, slots=True)
class _Sources:
    registry: LaneRegistryStore
    execution: ExecutionStore
    session: Path


def test_finalizes_flat_intraday_snapshot_and_exact_replay(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    readiness = _flat_readiness(equity="30010", last_equity="29990")

    preflight = preflight_intraday_lane_day(
        sources.registry,
        sources.execution,
        sources.session,
        SESSION_DATE,
        evaluated_at=EVALUATED_AT,
    )
    created = finalize_intraday_lane_day(
        sources.registry,
        sources.execution,
        sources.session,
        SESSION_DATE,
        readiness,
        evaluated_at=EVALUATED_AT,
    )
    replayed = finalize_intraday_lane_day(
        sources.registry,
        sources.execution,
        sources.session,
        SESSION_DATE,
        _flat_readiness(
            observed_at=OBSERVED_AT + dt.timedelta(minutes=1),
            equity="30010",
            last_equity="29990",
        ),
        evaluated_at=EVALUATED_AT + dt.timedelta(minutes=1),
    )

    assert preflight.manifest_key == lane_manifest_key(INTRADAY_MANIFEST)
    assert preflight.experiment_scope_key == ORB_SCOPE_KEY
    assert created.created is True
    assert replayed.created is False
    assert replayed.snapshot == created.snapshot
    assert created.snapshot.finalized_at == EVALUATED_AT
    assert created.snapshot.open_order_count == 0
    assert created.snapshot.open_position_count == 0
    assert created.snapshot.planned_open_risk == 0
    assert created.snapshot.champion_strategy_versions == ()
    assert created.snapshot.allocation_eligible is False
    assert created.snapshot.data_quality_complete is True
    assert created.snapshot.conservative_equity == Decimal("29990")
    assert created.snapshot.realized_pnl == Decimal("20")
    assert len(sources.registry.daily_snapshots()) == 1


@pytest.mark.parametrize(
    "case",
    (
        "before_close",
        "market_open",
        "blocked_readiness",
        "stale_readiness",
        "open_order",
        "nonzero_position",
        "wrong_account",
        "missing_manifest",
        "missing_scope",
        "missing_binding",
        "wrong_daily_scope",
        "missing_parent_ledger",
    ),
)
def test_finalization_gates_fail_without_appending_snapshot(
    tmp_path: Path,
    case: str,
) -> None:
    registry_parts = {
        "missing_manifest": (False, True, False),
        "missing_scope": (True, False, True),
        "missing_binding": (True, True, False),
    }.get(case, (True, True, True))
    sources = _sources(
        tmp_path,
        manifest=registry_parts[0],
        scope=registry_parts[1],
        binding=registry_parts[2],
        daily_strategy=(StrategyMode.VWAP_RECLAIM if case == "wrong_daily_scope" else StrategyMode.ORB),
    )
    readiness = _flat_readiness()
    evaluated_at = EVALUATED_AT
    if case == "before_close":
        before_close = SESSION_CLOSE - dt.timedelta(seconds=1)
        readiness = _flat_readiness(observed_at=before_close)
        evaluated_at = before_close
    elif case == "market_open":
        readiness = replace(
            readiness,
            market_clock=replace(readiness.market_clock, is_open=True),
        )
    elif case == "blocked_readiness":
        readiness = replace(readiness, runtime_reasons=("blocked",))
    elif case == "stale_readiness":
        readiness = _flat_readiness(observed_at=EVALUATED_AT - dt.timedelta(seconds=6))
    elif case == "open_order":
        readiness = replace(
            readiness,
            broker_state=replace(
                readiness.broker_state,
                open_orders=(_open_order(),),
            ),
        )
    elif case == "nonzero_position":
        readiness = replace(
            readiness,
            broker_state=replace(
                readiness.broker_state,
                positions=(PaperPositionSnapshot("DEMO", Decimal(1), Decimal("10")),),
            ),
        )
    elif case == "wrong_account":
        readiness = replace(
            readiness,
            broker_state=replace(
                readiness.broker_state,
                account=replace(
                    readiness.broker_state.account,
                    account_fingerprint=OTHER_FINGERPRINT,
                ),
            ),
        )
    elif case == "missing_parent_ledger":
        (sources.session.parent / "daily_research_ledger.jsonl").unlink()

    with pytest.raises(InvalidIntradayLaneFinalizationError) as caught:
        _ = finalize_intraday_lane_day(
            sources.registry,
            sources.execution,
            sources.session,
            SESSION_DATE,
            readiness,
            evaluated_at=evaluated_at,
        )

    assert sources.registry.daily_snapshots() == ()
    assert str(sources.execution.path) not in str(caught.value)
    assert str(FINGERPRINT) not in str(caught.value)


def test_incomplete_daily_quality_finalizes_as_ineligible_with_incident(
    tmp_path: Path,
) -> None:
    sources = _sources(
        tmp_path,
        quality_complete=False,
        daily_incidents=("fixture_quality_gap",),
    )

    result = finalize_intraday_lane_day(
        sources.registry,
        sources.execution,
        sources.session,
        SESSION_DATE,
        _flat_readiness(),
        evaluated_at=EVALUATED_AT,
    )

    assert result.created is True
    assert result.snapshot.data_quality_complete is False
    assert result.snapshot.incidents == (
        "data_quality_incomplete",
        "fixture_quality_gap",
    )
    assert result.snapshot.champion_strategy_versions == ()
    assert result.snapshot.allocation_eligible is False


def test_changed_pnl_conflicts_with_finalized_snapshot(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    _ = finalize_intraday_lane_day(
        sources.registry,
        sources.execution,
        sources.session,
        SESSION_DATE,
        _flat_readiness(),
        evaluated_at=EVALUATED_AT,
    )

    with pytest.raises(LaneRegistryConflictError):
        _ = finalize_intraday_lane_day(
            sources.registry,
            sources.execution,
            sources.session,
            SESSION_DATE,
            _flat_readiness(
                observed_at=OBSERVED_AT + dt.timedelta(minutes=1),
                equity="30001",
            ),
            evaluated_at=EVALUATED_AT + dt.timedelta(minutes=1),
        )

    assert len(sources.registry.daily_snapshots()) == 1


def test_changed_execution_ledger_identity_conflicts_with_snapshot(
    tmp_path: Path,
) -> None:
    sources = _sources(tmp_path)
    readiness = _flat_readiness()
    _ = finalize_intraday_lane_day(
        sources.registry,
        sources.execution,
        sources.session,
        SESSION_DATE,
        readiness,
        evaluated_at=EVALUATED_AT,
    )
    _append_completed_intent(sources.execution)

    with pytest.raises(LaneRegistryConflictError):
        _ = finalize_intraday_lane_day(
            sources.registry,
            sources.execution,
            sources.session,
            SESSION_DATE,
            _flat_readiness(observed_at=OBSERVED_AT + dt.timedelta(minutes=1)),
            evaluated_at=EVALUATED_AT + dt.timedelta(minutes=1),
        )

    assert len(sources.registry.daily_snapshots()) == 1


def _sources(
    tmp_path: Path,
    *,
    manifest: bool = True,
    scope: bool = True,
    binding: bool = True,
    daily_strategy: StrategyMode = StrategyMode.ORB,
    quality_complete: bool = True,
    daily_incidents: tuple[str, ...] = (),
) -> _Sources:
    execution = ExecutionStore(tmp_path / "execution.sqlite3")
    with execution.writer() as writer:
        assert writer.bind_account(FINGERPRINT, BOUND_AT) is True

    registry = LaneRegistryStore(tmp_path / "lane-registry.sqlite3")
    with registry.writer() as writer:
        if manifest:
            assert writer.register_manifest(INTRADAY_MANIFEST) is True
        if scope:
            assert writer.register_experiment_scope(ORB_SCOPE) is True
        if binding:
            ledger_fingerprint = hashlib.sha256(str(execution.path).encode()).hexdigest()
            assert (
                writer.bind_account(
                    lane_account_binding(
                        INTRADAY_MANIFEST,
                        FINGERPRINT,
                        ledger_fingerprint,
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
        daily_strategy,
        "test-code",
        SESSION_CLOSE + dt.timedelta(minutes=2),
    )
    if not quality_complete or daily_incidents:
        quality = record.session_quality.model_copy(update={"forward_day_eligible": quality_complete})
        record = record.model_copy(update={"session_quality": quality, "incidents": daily_incidents})
    assert write_daily_record(session, record) is True
    return _Sources(registry, execution, session)


def _flat_readiness(
    *,
    observed_at: dt.datetime = OBSERVED_AT,
    equity: str = "30000",
    last_equity: str = "30000",
) -> PaperRuntimeReadiness:
    account = PaperAccountSnapshot(
        observed_at=observed_at,
        status="ACTIVE",
        trading_blocked=False,
        equity=Decimal(equity),
        last_equity=Decimal(last_equity),
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
            next_close=dt.datetime(2026, 7, 15, 20, tzinfo=dt.UTC),
        ),
        stream_heartbeat=PaperOrderStreamHeartbeat(
            connection_epoch=PaperStreamEpoch("test-epoch"),
            authorized_at=observed_at - dt.timedelta(seconds=2),
            subscribed_at=observed_at - dt.timedelta(seconds=1),
            pong_at=observed_at,
        ),
        reconciliation=ReconciliationResult(True, ()),
        portfolio=CompletePaperPortfolio(
            observed_at=observed_at,
            account_status=account.status,
            trading_blocked=account.trading_blocked,
            equity=account.equity,
            last_equity=account.last_equity,
            buying_power=account.buying_power,
            exposures=(),
        ),
    )


def _open_order() -> PaperOrderSnapshot:
    return PaperOrderSnapshot(
        broker_order_id=BrokerOrderId("paper-order"),
        client_order_id=IntentId("paper-intent"),
        symbol="DEMO",
        side=PaperOrderSide.BUY,
        status="new",
        quantity=Decimal(1),
        filled_quantity=Decimal(0),
        limit_price=Decimal("10"),
        time_in_force="day",
        extended_hours=False,
    )


def _append_completed_intent(execution: ExecutionStore) -> None:
    intent = PaperOrderIntent(
        intent_id=IntentId("completed-intent"),
        strategy_id="orb",
        strategy_version="orb-v1",
        symbol="DEMO",
        created_at=SESSION_CLOSE - dt.timedelta(hours=1),
        side=PaperOrderSide.BUY,
        entry_limit=10.0,
        stop=9.5,
        target_1r=10.5,
        target_2r=11.0,
    )
    event = BrokerOrderEvent(
        event_key=BrokerEventKey("completed-fill"),
        intent_id=intent.intent_id,
        occurred_at=SESSION_CLOSE - dt.timedelta(minutes=30),
        event_type=BrokerOrderEventType.FILL,
        broker_order_id=BrokerOrderId("completed-paper-order"),
        payload_json="{}",
    )
    with execution.writer() as writer:
        assert writer.save_intent(intent, 1) is True
        assert writer.append_broker_event(event, account_fingerprint=FINGERPRINT) is True
