from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

from tests.paper_runtime_fixtures import (
    FakeLedgerReader,
    FakeReadyStream,
    account,
    candidate,
    credentials,
    latest_bar,
    ledger,
    market_clock,
    partial_state,
    stream_opener,
)
from trading_agent.paper_execution_models import BrokerOrderId, PaperOrderSide
from trading_agent.paper_order_gate_models import PaperOrderGateState
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoClientOrderId,
    ProtectiveOcoExitPlan,
    ProtectiveOcoLegKind,
    ProtectiveOcoLegSnapshot,
    ProtectiveOcoOrderType,
    ProtectiveOcoSnapshot,
)
from trading_agent.paper_protective_oco_store import (
    StoredProtectiveOcoPlan,
    protective_oco_plan_key,
)
from trading_agent.paper_runtime_session import _open_paper_runtime_session


def _plan() -> ProtectiveOcoExitPlan:
    return ProtectiveOcoExitPlan(
        ProtectiveOcoClientOrderId("protect-" + "a" * 40),
        ledger(with_existing=True).intents[0].intent_id,
        "MSFT",
        PaperOrderSide.SELL,
        20,
        Decimal("102"),
        Decimal("99"),
    )


def _snapshot(plan: ProtectiveOcoExitPlan) -> ProtectiveOcoSnapshot:
    return ProtectiveOcoSnapshot(
        account().observed_at,
        ProtectiveOcoLegSnapshot(
            ProtectiveOcoLegKind.TAKE_PROFIT,
            BrokerOrderId("tp-1"),
            plan.client_order_id,
            "MSFT",
            plan.side,
            "new",
            Decimal(20),
            Decimal(0),
            ProtectiveOcoOrderType.LIMIT,
            Decimal("102"),
            None,
            "day",
            False,
        ),
        ProtectiveOcoLegSnapshot(
            ProtectiveOcoLegKind.STOP_LOSS,
            BrokerOrderId("stop-1"),
            "stop-child-1",
            "MSFT",
            plan.side,
            "new",
            Decimal(20),
            Decimal(0),
            ProtectiveOcoOrderType.STOP,
            None,
            Decimal("99"),
            "day",
            False,
        ),
    )


def _protected_ledger(plan: ProtectiveOcoExitPlan):
    return replace(
        ledger(with_existing=True),
        protective_oco_plans=(
            StoredProtectiveOcoPlan(
                protective_oco_plan_key(plan),
                account().observed_at.isoformat(),
                plan,
            ),
        ),
    )


def _evaluate(snapshot: ProtectiveOcoSnapshot) -> PaperOrderGateState:
    plan = _plan()
    protected_state = replace(partial_state(), protective_ocos=(snapshot,))
    stream = FakeReadyStream()
    with _open_paper_runtime_session(
        credentials(),
        FakeLedgerReader(stream, _protected_ledger(plan)),
        state_loader=lambda _: (protected_state, market_clock()),
        stream_opener=stream_opener(stream),
        _clock=lambda: dt.datetime(2026, 7, 14, 13, 36, 3, tzinfo=dt.UTC),
    ) as session:
        decision = session.evaluate_order(
            latest_bar=latest_bar(),
            candidate_intent=candidate(),
            liquidity_allowed_quantity=1_000,
            estimated_spread_bps=0.0,
        )
    return decision.state


def test_live_session_accepts_exact_nested_oco_coverage_for_partial_fill() -> None:
    # Given: one partial position, its immutable plan, and exact live parent/stop legs.
    snapshot = _snapshot(_plan())

    # When: admission reconciles the current broker inventory inside the heartbeat pair.
    state = _evaluate(snapshot)

    # Then: verified exact protection no longer causes the portfolio blocker.
    assert state is PaperOrderGateState.APPROVED


def test_live_session_blocks_a_stop_leg_with_the_wrong_price() -> None:
    # Given: an otherwise exact OCO whose broker stop differs from the immutable plan.
    snapshot = _snapshot(_plan())
    wrong_stop = replace(snapshot.stop_loss, stop_price=Decimal("98"))

    # When: admission reconciles the mismatched nested legs.
    state = _evaluate(replace(snapshot, stop_loss=wrong_stop))

    # Then: protection remains fail-closed at the portfolio boundary.
    assert state is PaperOrderGateState.PORTFOLIO_BLOCKED


def test_live_session_rejects_protection_observed_before_the_heartbeat() -> None:
    # Given: structurally valid OCO legs carrying an old REST receipt timestamp.
    stale = replace(
        _snapshot(_plan()),
        observed_at=dt.datetime(2026, 7, 14, 13, 35, 50, tzinfo=dt.UTC),
    )

    # When: admission evaluates that stale protection as current evidence.
    state = _evaluate(stale)

    # Then: current-epoch REST reconciliation blocks before portfolio approval.
    assert state is PaperOrderGateState.RECONCILIATION_BLOCKED
