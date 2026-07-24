from __future__ import annotations

import datetime as dt
from decimal import Decimal

from trading_agent.broker_order_projection import BrokerOrderLedgerState
from trading_agent.execution_schema import StoredIntent
from trading_agent.metrics import PaperTrade
from trading_agent.models import RecommendationState
from trading_agent.paper_account_activity_store import StoredPaperAccountActivity
from trading_agent.paper_execution_models import (
    AccountActivityId,
    AccountFingerprint,
    BrokerOrderId,
    IntentId,
    PaperOrderSide,
    PaperTradeActivity,
    PaperTradeActivityType,
)
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoClientOrderId,
    ProtectiveOcoExitPlan,
    ProtectiveOcoLegKind,
    ProtectiveOcoLegSnapshot,
    ProtectiveOcoOrderType,
    ProtectiveOcoSnapshot,
)
from trading_agent.paper_protective_oco_recovery_store import StoredProtectiveOcoSnapshot
from trading_agent.paper_protective_oco_store import ProtectiveOcoPlanKey, StoredProtectiveOcoPlan
from trading_agent.paper_stream_recovery_models import PaperStreamRecoveryKey

REVIEWED_AT = dt.datetime(2026, 7, 24, 6, 0, tzinfo=dt.UTC)
ENTRY_AT = dt.datetime(2026, 7, 14, 14, 0, tzinfo=dt.UTC)
EXIT_AT = ENTRY_AT + dt.timedelta(hours=1)


def shadow_trade() -> PaperTrade:
    return PaperTrade(
        "recommendation-1",
        "FAST",
        "opening_range_breakout",
        ENTRY_AT,
        EXIT_AT,
        10.0,
        12.0,
        0.2,
        RecommendationState.TARGET_2R,
        False,
    )


def intent() -> StoredIntent:
    return StoredIntent(
        IntentId("recommendation-1"),
        "opening_range_breakout",
        "orb-v1",
        "FAST",
        (ENTRY_AT - dt.timedelta(minutes=1)).isoformat(),
        PaperOrderSide.BUY,
        Decimal("10"),
        Decimal("9.5"),
        Decimal("11"),
        Decimal("12"),
        2,
    )


def entry_state() -> BrokerOrderLedgerState:
    return BrokerOrderLedgerState(
        intent_id=IntentId("recommendation-1"),
        broker_order_ids=(BrokerOrderId("entry-order"),),
        terminal_event_types=(),
        cumulative_filled_quantity=Decimal(2),
        complete_fill=True,
        terminal=True,
        has_fill_evidence=True,
        anomaly_reasons=(),
        execution_detail_complete=True,
        execution_average_price=Decimal("10.1"),
    )


def protective_exit() -> tuple[StoredProtectiveOcoPlan, StoredProtectiveOcoSnapshot]:
    key = ProtectiveOcoPlanKey("p" * 64)
    plan = ProtectiveOcoExitPlan(
        ProtectiveOcoClientOrderId("protective-1"),
        IntentId("recommendation-1"),
        "FAST",
        PaperOrderSide.SELL,
        2,
        Decimal("12"),
        Decimal("9.5"),
    )
    take_profit = ProtectiveOcoLegSnapshot(
        ProtectiveOcoLegKind.TAKE_PROFIT,
        BrokerOrderId("take-profit-order"),
        "protective-1",
        "FAST",
        PaperOrderSide.SELL,
        "filled",
        Decimal(2),
        Decimal(2),
        ProtectiveOcoOrderType.LIMIT,
        Decimal("12"),
        None,
        "day",
        False,
    )
    stop_loss = ProtectiveOcoLegSnapshot(
        ProtectiveOcoLegKind.STOP_LOSS,
        BrokerOrderId("stop-order"),
        "protective-1-stop",
        "FAST",
        PaperOrderSide.SELL,
        "canceled",
        Decimal(2),
        Decimal(0),
        ProtectiveOcoOrderType.STOP,
        None,
        Decimal("9.5"),
        "day",
        False,
    )
    snapshot = ProtectiveOcoSnapshot(EXIT_AT, take_profit, stop_loss)
    return (
        StoredProtectiveOcoPlan(key, (ENTRY_AT + dt.timedelta(minutes=1)).isoformat(), plan),
        StoredProtectiveOcoSnapshot(1, PaperStreamRecoveryKey("recovery-1"), key, snapshot),
    )


def exit_activity() -> StoredPaperAccountActivity:
    activity = PaperTradeActivity(
        AccountActivityId("exit-activity"),
        BrokerOrderId("take-profit-order"),
        "FAST",
        PaperOrderSide.SELL,
        PaperTradeActivityType.FILL,
        Decimal(2),
        Decimal(2),
        Decimal(0),
        Decimal("11.9"),
        EXIT_AT,
        '{"safe":"fixture"}',
    )
    return StoredPaperAccountActivity(
        1,
        PaperStreamRecoveryKey("recovery-1"),
        AccountFingerprint("account"),
        activity,
    )
