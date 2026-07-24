from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Final

from trading_agent.intraday_broker_shadow_models import (
    BrokerShadowAssessment,
    BrokerShadowEvidenceStatus,
    BrokerShadowMetrics,
    BrokerShadowTradePair,
)
from trading_agent.metrics import day_block_bootstrap_interval

MINIMUM_PAIRED_SESSIONS: Final = 60
MINIMUM_PAIRED_TRADES: Final = 100
MINIMUM_PROFIT_FACTOR: Final = 1.15
BOOTSTRAP_SAMPLES: Final = 2_000
BOOTSTRAP_SEED: Final = 20_260_724


def assess_broker_shadow_pairs(
    pairs: tuple[BrokerShadowTradePair, ...],
    unpaired_broker_intent_count: int,
) -> BrokerShadowAssessment:
    broker = _metrics(pairs, lambda pair: pair.broker_net_return)
    shadow = _metrics(pairs, lambda pair: pair.shadow_net_return)
    session_count = len({pair.session_date for pair in pairs})
    blockers: list[str] = []
    if session_count < MINIMUM_PAIRED_SESSIONS:
        blockers.append(
            f"minimum_paired_sessions:{session_count}/{MINIMUM_PAIRED_SESSIONS}"
        )
    if len(pairs) < MINIMUM_PAIRED_TRADES:
        blockers.append(
            f"minimum_paired_trades:{len(pairs)}/{MINIMUM_PAIRED_TRADES}"
        )
    if unpaired_broker_intent_count:
        blockers.append(
            f"unpaired_broker_intents:{unpaired_broker_intent_count}"
        )
    mature = (
        session_count >= MINIMUM_PAIRED_SESSIONS
        and len(pairs) >= MINIMUM_PAIRED_TRADES
    )
    if mature:
        blockers.extend(_metric_blockers("broker", broker))
        blockers.extend(_metric_blockers("shadow", shadow))
    ordered = tuple(sorted(set(blockers)))
    if not mature:
        status = BrokerShadowEvidenceStatus.COLLECTING
    elif ordered:
        status = BrokerShadowEvidenceStatus.NOT_CONFIRMED
    else:
        status = BrokerShadowEvidenceStatus.READY
    return BrokerShadowAssessment(status, ordered, broker, shadow)


def _metrics(
    pairs: tuple[BrokerShadowTradePair, ...],
    value: Callable[[BrokerShadowTradePair], float],
) -> BrokerShadowMetrics:
    if not pairs:
        return BrokerShadowMetrics(
            trade_count=0,
            average_return=None,
            profit_factor=None,
            mean_ci_low=None,
            mean_ci_high=None,
        )
    returns = tuple(value(pair) for pair in pairs)
    wins = tuple(item for item in returns if item > 0)
    losses = tuple(item for item in returns if item < 0)
    by_date: dict[dt.date, list[float]] = {}
    for pair, item in zip(pairs, returns, strict=True):
        by_date.setdefault(pair.session_date, []).append(item)
    lower, upper = day_block_bootstrap_interval(
        tuple(tuple(block) for block in by_date.values()),
        BOOTSTRAP_SAMPLES,
        BOOTSTRAP_SEED,
    )
    return BrokerShadowMetrics(
        trade_count=len(returns),
        average_return=sum(returns) / len(returns),
        profit_factor=None if not losses else sum(wins) / abs(sum(losses)),
        mean_ci_low=lower,
        mean_ci_high=upper,
    )


def _metric_blockers(
    prefix: str,
    metrics: BrokerShadowMetrics,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if (
        metrics.profit_factor is None
        or metrics.profit_factor < MINIMUM_PROFIT_FACTOR
    ):
        blockers.append(f"{prefix}_pf_below_1.15")
    if metrics.average_return is None or metrics.average_return <= 0:
        blockers.append(f"{prefix}_average_nonpositive")
    if metrics.mean_ci_low is None or metrics.mean_ci_low < 0:
        blockers.append(f"{prefix}_ci_lower_below_zero")
    return tuple(blockers)


__all__ = (
    "MINIMUM_PAIRED_SESSIONS",
    "MINIMUM_PAIRED_TRADES",
    "assess_broker_shadow_pairs",
)
