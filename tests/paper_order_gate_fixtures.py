from __future__ import annotations

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from trading_agent.alpaca_paper_order_stream import (
    PaperOrderStreamHeartbeat,
    PaperStreamEpoch,
)
from trading_agent.paper_execution_models import (
    IntentId,
    PaperMarketClockSnapshot,
    PaperOrderIntent,
    PaperOrderSide,
)
from trading_agent.paper_order_gate import _evaluate_reconciled_paper_order_gate
from trading_agent.paper_order_gate_models import (
    CompletePaperPortfolio,
    LatestCompletedBar,
    PaperExposureKind,
    PaperOrderGateDecision,
    PaperOrderGateSnapshot,
    PaperPortfolioExposure,
)

NEW_YORK = ZoneInfo("America/New_York")
EPOCH = PaperStreamEpoch("epoch-1")


def at(hour: int, minute: int, second: int = 0) -> dt.datetime:
    return dt.datetime(2026, 7, 14, hour, minute, second, tzinfo=NEW_YORK)


def candidate(created_at: dt.datetime | None = None) -> PaperOrderIntent:
    return PaperOrderIntent(
        intent_id=IntentId("intent-1"),
        strategy_id="orb",
        strategy_version="1",
        symbol="AAPL",
        created_at=at(9, 36, 3) if created_at is None else created_at,
        side=PaperOrderSide.BUY,
        entry_limit=100.0,
        stop=99.0,
        target_1r=101.0,
        target_2r=102.0,
    )


def exposure(
    symbol: str,
    *,
    gross: str = "5000",
    risk: str = "75",
    kind: PaperExposureKind = PaperExposureKind.OPEN_POSITION,
) -> PaperPortfolioExposure:
    return PaperPortfolioExposure(
        intent_id=IntentId(f"existing-{symbol}"),
        symbol=symbol,
        kind=kind,
        gross_exposure=Decimal(gross),
        planned_risk=Decimal(risk),
    )


def snapshot() -> PaperOrderGateSnapshot:
    return PaperOrderGateSnapshot(
        market_clock=PaperMarketClockSnapshot(
            observed_at=at(9, 36, 4),
            market_timestamp=at(9, 36, 4),
            is_open=True,
            next_open=at(9, 30) + dt.timedelta(days=1),
            next_close=at(16, 0),
        ),
        latest_bar=LatestCompletedBar(
            symbol="AAPL",
            started_at=at(9, 35),
            first_observed_at=at(9, 36, 2),
        ),
        stream_heartbeat=PaperOrderStreamHeartbeat(
            connection_epoch=EPOCH,
            authorized_at=at(9, 36),
            subscribed_at=at(9, 36),
            pong_at=at(9, 36, 4),
        ),
        portfolio=CompletePaperPortfolio(
            observed_at=at(9, 36, 4),
            account_status="ACTIVE",
            trading_blocked=False,
            equity=Decimal("30000"),
            last_equity=Decimal("30000"),
            buying_power=Decimal("60000"),
            exposures=(),
        ),
        candidate_intent=candidate(),
        liquidity_allowed_quantity=1_000,
        estimated_spread_bps=0.0,
    )


def evaluate(
    gate_snapshot: PaperOrderGateSnapshot,
    *,
    evaluated_at: dt.datetime | None = None,
) -> PaperOrderGateDecision:
    return _evaluate_reconciled_paper_order_gate(
        gate_snapshot,
        at(9, 36, 5) if evaluated_at is None else evaluated_at,
    )
