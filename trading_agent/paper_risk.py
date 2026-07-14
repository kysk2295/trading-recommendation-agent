from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, assert_never, override

from trading_agent.paper_execution_models import (
    PaperOrderIntent,
    PaperOrderSide,
    SizedPaperOrder,
)

BASIS_POINT_DENOMINATOR = Decimal("10000")
HARD_REFERENCE_EQUITY: Final = 30_000.0
HARD_MAX_RISK_DOLLARS: Final = 75.0
HARD_RISK_FRACTION: Final = 0.0025
HARD_MAX_NOTIONAL_DOLLARS: Final = 6_000.0
HARD_MAX_OPEN_POSITIONS: Final = 3
HARD_DAILY_LOSS_LIMIT_DOLLARS: Final = 300.0
HARD_MIN_PER_SIDE_COST_BPS: Final = 20.0


class UnsafePaperRiskConfigError(ValueError):
    @override
    def __str__(self) -> str:
        return "Paper risk config가 승인된 하드 한도를 완화하거나 유효하지 않습니다"


@dataclass(frozen=True, slots=True)
class PaperRiskConfig:
    reference_equity: float = HARD_REFERENCE_EQUITY
    max_risk_dollars: float = HARD_MAX_RISK_DOLLARS
    risk_fraction: float = HARD_RISK_FRACTION
    max_notional_dollars: float = HARD_MAX_NOTIONAL_DOLLARS
    max_open_positions: int = HARD_MAX_OPEN_POSITIONS
    daily_loss_limit_dollars: float = HARD_DAILY_LOSS_LIMIT_DOLLARS
    per_side_cost_bps: float = HARD_MIN_PER_SIDE_COST_BPS

    def assert_within_hard_limits(self) -> None:
        finite_values = (
            self.reference_equity,
            self.max_risk_dollars,
            self.risk_fraction,
            self.max_notional_dollars,
            self.daily_loss_limit_dollars,
            self.per_side_cost_bps,
        )
        if (
            not all(math.isfinite(value) for value in finite_values)
            or not 0 < self.reference_equity <= HARD_REFERENCE_EQUITY
            or not 0 < self.max_risk_dollars <= HARD_MAX_RISK_DOLLARS
            or not 0 < self.risk_fraction <= HARD_RISK_FRACTION
            or not 0 < self.max_notional_dollars <= HARD_MAX_NOTIONAL_DOLLARS
            or not 0 < self.max_open_positions <= HARD_MAX_OPEN_POSITIONS
            or not 0 < self.daily_loss_limit_dollars <= HARD_DAILY_LOSS_LIMIT_DOLLARS
            or self.per_side_cost_bps < HARD_MIN_PER_SIDE_COST_BPS
        ):
            raise UnsafePaperRiskConfigError


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
    config.assert_within_hard_limits()
    market_values = (
        intent.entry_limit,
        intent.stop,
        intent.target_1r,
        intent.target_2r,
        context.conservative_equity,
        context.estimated_spread_bps,
    )
    if not all(math.isfinite(value) for value in market_values):
        return None
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
