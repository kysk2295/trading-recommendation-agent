from __future__ import annotations

from dataclasses import dataclass

from trading_agent.models import TradePlan


@dataclass(frozen=True, slots=True)
class RiskConfig:
    max_spread_bps: float = 100.0
    max_risk_pct: float = 0.05
    target_2r_multiple: float = 2.0


def build_trade_plan(entry: float, stop: float, spread_bps: float, config: RiskConfig) -> TradePlan | None:
    risk = entry - stop
    if risk <= 0.0 or spread_bps > config.max_spread_bps or risk / entry > config.max_risk_pct:
        return None
    return TradePlan(
        entry,
        stop,
        entry + risk,
        entry + risk * config.target_2r_multiple,
        risk,
    )
