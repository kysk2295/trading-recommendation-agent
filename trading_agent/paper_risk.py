from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, assert_never

from trading_agent.paper_execution_models import (
    PaperOrderIntent,
    PaperOrderSide,
    SizedPaperOrder,
)

BASIS_POINT_DENOMINATOR = Decimal("10000")


@dataclass(frozen=True, slots=True)
class PaperRiskConfig:
    reference_equity: float = 30_000.0
    max_risk_dollars: float = 75.0
    risk_fraction: float = 0.0025
    max_notional_dollars: float = 6_000.0
    max_open_positions: int = 3
    daily_loss_limit_dollars: float = 300.0
    per_side_cost_bps: float = 20.0


@dataclass(frozen=True, slots=True)
class PaperSizingContext:
    conservative_equity: float
    liquidity_allowed_quantity: int
    estimated_spread_bps: float


DEFAULT_PAPER_RISK_CONFIG: Final = PaperRiskConfig()


def size_paper_order(
    intent: PaperOrderIntent,
    context: PaperSizingContext,
    config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
) -> SizedPaperOrder | None:
    entry = Decimal(str(intent.entry_limit))
    stop = Decimal(str(intent.stop))
    equity = Decimal(str(context.conservative_equity))
    spread_bps = Decimal(str(context.estimated_spread_bps))
    cost_bps = Decimal(str(config.per_side_cost_bps))
    if (
        entry <= 0
        or stop <= 0
        or equity <= 0
        or context.liquidity_allowed_quantity <= 0
        or spread_bps < 0
        or cost_bps < 0
    ):
        return None

    match intent.side:
        case PaperOrderSide.BUY:
            stop_distance = entry - stop
        case PaperOrderSide.SELL:
            stop_distance = stop - entry
        case unreachable:
            assert_never(unreachable)
    if stop_distance <= 0:
        return None

    spread_reserve = entry * spread_bps / BASIS_POINT_DENOMINATOR
    cost_reserve = (entry + stop) * cost_bps / BASIS_POINT_DENOMINATOR
    risk_per_share = stop_distance + spread_reserve + cost_reserve
    risk_budget = min(
        Decimal(str(config.max_risk_dollars)),
        equity * Decimal(str(config.risk_fraction)),
    )
    risk_quantity = math.floor(risk_budget / risk_per_share)
    notional_quantity = math.floor(
        Decimal(str(config.max_notional_dollars)) / entry
    )
    quantity = min(
        risk_quantity,
        notional_quantity,
        context.liquidity_allowed_quantity,
    )
    if quantity <= 0:
        return None
    return SizedPaperOrder(
        intent=intent,
        quantity=quantity,
        risk_per_share=float(risk_per_share),
        planned_risk=float(risk_per_share * quantity),
        notional=float(entry * quantity),
    )
