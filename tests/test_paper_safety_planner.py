from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerOrderId,
    IntentId,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperMarketClockSnapshot,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_order_gate_models import (
    CompletePaperPortfolio,
    PaperExposureKind,
    PaperPortfolioExposure,
)
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoLegKind,
    ProtectiveOcoLegSnapshot,
    ProtectiveOcoOrderType,
    ProtectiveOcoSnapshot,
)
from trading_agent.paper_safety_models import (
    BlockedPaperSafetyPlan,
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)
from trading_agent.paper_safety_planner import plan_paper_safety_actions
from trading_agent.us_equity_calendar import NEW_YORK


def _at(hour: int, minute: int, second: int = 0) -> dt.datetime:
    return dt.datetime(2026, 7, 14, hour, minute, second, tzinfo=NEW_YORK)


def _state(now: dt.datetime) -> PaperBrokerState:
    account = PaperAccountSnapshot(
        now,
        "ACTIVE",
        False,
        Decimal("30000"),
        Decimal("30000"),
        Decimal("60000"),
        AccountFingerprint("a" * 64),
    )
    order = PaperOrderSnapshot(
        BrokerOrderId("entry-1"),
        IntentId("intent-1"),
        "AAA",
        PaperOrderSide.BUY,
        "partially_filled",
        Decimal(20),
        Decimal(10),
        Decimal("10"),
        "day",
        False,
    )
    position = PaperPositionSnapshot("AAA", Decimal(10), Decimal(100))
    take_profit = ProtectiveOcoLegSnapshot(
        ProtectiveOcoLegKind.TAKE_PROFIT,
        BrokerOrderId("oco-parent-1"),
        "protect-1",
        "AAA",
        PaperOrderSide.SELL,
        "new",
        Decimal(10),
        Decimal(0),
        ProtectiveOcoOrderType.LIMIT,
        Decimal("10.5"),
        None,
        "day",
        False,
    )
    stop = replace(
        take_profit,
        kind=ProtectiveOcoLegKind.STOP_LOSS,
        broker_order_id=BrokerOrderId("oco-stop-1"),
        client_order_id="stop-1",
        order_type=ProtectiveOcoOrderType.STOP,
        limit_price=None,
        stop_price=Decimal("9.75"),
    )
    return PaperBrokerState(
        account,
        (order,),
        (position,),
        (ProtectiveOcoSnapshot(now, take_profit, stop),),
    )


def _clock(now: dt.datetime) -> PaperMarketClockSnapshot:
    return PaperMarketClockSnapshot(
        now,
        now,
        True,
        _at(9, 30) + dt.timedelta(days=1),
        _at(16, 0),
    )


def _portfolio(now: dt.datetime) -> CompletePaperPortfolio:
    return CompletePaperPortfolio(
        now,
        "ACTIVE",
        False,
        Decimal("30000"),
        Decimal("30000"),
        Decimal("60000"),
        (
            PaperPortfolioExposure(
                IntentId("intent-1"),
                "AAA",
                PaperExposureKind.PARTIAL_ENTRY,
                Decimal(200),
                Decimal(75),
            ),
        ),
    )


def test_entry_cutoff_cancels_the_remaining_entry_without_flattening() -> None:
    now = _at(15, 30)

    plan = plan_paper_safety_actions(_state(now), _clock(now), _portfolio(now), now)

    assert isinstance(plan, PaperSafetyPlan)
    assert plan.account_fingerprint == AccountFingerprint("a" * 64)
    assert plan.phase is PaperSafetyPhase.ENTRY_CUTOFF
    assert plan.actions == (PaperCancelOrderAction(BrokerOrderId("entry-1"), "AAA", False),)


def test_eod_window_cancels_orders_and_oco_before_flattening() -> None:
    now = _at(15, 55)

    plan = plan_paper_safety_actions(_state(now), _clock(now), _portfolio(now), now)

    assert isinstance(plan, PaperSafetyPlan)
    assert plan.phase is PaperSafetyPhase.EOD_FLATTEN
    assert plan.actions == (
        PaperCancelOrderAction(BrokerOrderId("entry-1"), "AAA", False),
        PaperCancelOrderAction(BrokerOrderId("oco-parent-1"), "AAA", True),
        PaperClosePositionAction("AAA", PaperOrderSide.SELL, Decimal(10)),
    )


def test_conservative_open_risk_triggers_the_daily_loss_kill_switch() -> None:
    now = _at(14, 0)
    state = _state(now)
    state = replace(
        state,
        account=replace(state.account, equity=Decimal("29774")),
    )

    portfolio = replace(_portfolio(now), equity=Decimal("29774"))
    plan = plan_paper_safety_actions(state, _clock(now), portfolio, now)

    assert isinstance(plan, PaperSafetyPlan)
    assert plan.phase is PaperSafetyPhase.KILL_SWITCH
    assert plan.mark_to_market_daily_pnl == Decimal("-226")
    assert plan.conservative_daily_pnl == Decimal("-301")
    assert isinstance(plan.actions[-1], PaperClosePositionAction)


def test_daily_kill_latch_reasserts_flattening_after_equity_recovers() -> None:
    now = _at(14, 0)

    plan = plan_paper_safety_actions(
        _state(now),
        _clock(now),
        _portfolio(now),
        now,
        kill_switch_latched=True,
    )

    assert isinstance(plan, PaperSafetyPlan)
    assert plan.phase is PaperSafetyPhase.KILL_SWITCH
    assert isinstance(plan.actions[-1], PaperClosePositionAction)


def test_safety_planner_rejects_a_stale_broker_clock() -> None:
    now = _at(15, 55)
    stale = replace(_clock(now), observed_at=now - dt.timedelta(seconds=6))

    decision = plan_paper_safety_actions(_state(now), stale, _portfolio(now), now)

    assert isinstance(decision, BlockedPaperSafetyPlan)
    assert "현재" in decision.reasons[0]


def test_safety_planner_rejects_duplicate_cancel_order_identity() -> None:
    now = _at(15, 55)
    state = _state(now)
    duplicate = replace(
        state.protective_ocos[0],
        take_profit=replace(
            state.protective_ocos[0].take_profit,
            broker_order_id=BrokerOrderId("entry-1"),
        ),
    )

    decision = plan_paper_safety_actions(
        replace(state, protective_ocos=(duplicate,)),
        _clock(now),
        _portfolio(now),
        now,
    )

    assert isinstance(decision, BlockedPaperSafetyPlan)
    assert "중복" in decision.reasons[0]
