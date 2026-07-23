from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass
from typing import Final
from zoneinfo import ZoneInfo

from trading_agent.models import RecommendationState
from trading_agent.store import PaperStore

TERMINAL_TRADE_STATES: Final = frozenset(
    {
        RecommendationState.STOPPED,
        RecommendationState.TARGET_2R,
        RecommendationState.TIME_EXIT,
    }
)
NEW_YORK: Final = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class PaperTrade:
    recommendation_id: str
    symbol: str
    strategy: str
    entry_at: dt.datetime
    exit_at: dt.datetime
    entry: float
    exit: float
    gross_return: float
    exit_state: RecommendationState
    uses_close_fallback: bool


@dataclass(frozen=True, slots=True)
class MetricsConfig:
    side_cost_bps: int
    bootstrap_samples: int
    random_seed: int


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    side_cost_bps: int
    trade_count: int
    win_count: int
    win_rate: float | None
    average_return: float | None
    profit_factor: float | None
    cumulative_return: float | None
    max_drawdown: float | None
    fallback_exit_count: int
    fallback_exit_rate: float | None
    mean_ci_low: float | None
    mean_ci_high: float | None


def extract_paper_trades(stores: tuple[PaperStore, ...]) -> tuple[PaperTrade, ...]:
    trades: list[PaperTrade] = []
    seen: set[str] = set()
    for store in stores:
        recommendations = (
            row for row in store.recommendations() if row.state is not RecommendationState.CAUSALITY_EXCLUDED
        )
        for recommendation in recommendations:
            if recommendation.recommendation_id in seen:
                continue
            events = store.events(recommendation.recommendation_id)
            active = next(
                (event for event in events if event.state is RecommendationState.ACTIVE),
                None,
            )
            if active is None:
                continue
            terminal = next(
                (
                    event
                    for event in events
                    if event.occurred_at >= active.occurred_at
                    and event.state in TERMINAL_TRADE_STATES
                    and event.price is not None
                ),
                None,
            )
            if terminal is None or terminal.price is None:
                continue
            entry = recommendation.entry if active.price is None else active.price
            trades.append(
                PaperTrade(
                    recommendation.recommendation_id,
                    recommendation.symbol,
                    recommendation.strategy,
                    active.occurred_at,
                    terminal.occurred_at,
                    entry,
                    terminal.price,
                    round(terminal.price / entry - 1.0, 12),
                    terminal.state,
                    "마지막 완료 봉" in terminal.note,
                )
            )
            seen.add(recommendation.recommendation_id)
    return tuple(sorted(trades, key=lambda row: (row.exit_at, row.recommendation_id)))


def summarize_performance(
    trades: tuple[PaperTrade, ...],
    config: MetricsConfig,
) -> PerformanceMetrics:
    if not trades:
        return PerformanceMetrics(
            config.side_cost_bps,
            0,
            0,
            None,
            None,
            None,
            None,
            None,
            0,
            None,
            None,
            None,
        )
    returns = tuple(net_return(trade, config.side_cost_bps) for trade in trades)
    wins = tuple(value for value in returns if value > 0.0)
    losses = tuple(value for value in returns if value < 0.0)
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
    mean = sum(returns) / len(returns)
    blocks = _day_return_blocks(trades, returns)
    lower, upper = day_block_bootstrap_interval(
        blocks,
        config.bootstrap_samples,
        config.random_seed,
    )
    fallback_count = sum(trade.uses_close_fallback for trade in trades)
    return PerformanceMetrics(
        config.side_cost_bps,
        len(trades),
        len(wins),
        len(wins) / len(trades),
        mean,
        None if not losses else sum(wins) / abs(sum(losses)),
        equity - 1.0,
        max_drawdown,
        fallback_count,
        fallback_count / len(trades),
        lower,
        upper,
    )


def net_return(trade: PaperTrade, side_cost_bps: int) -> float:
    cost_rate = side_cost_bps / 10_000.0
    return trade.exit * (1.0 - cost_rate) / (trade.entry * (1.0 + cost_rate)) - 1.0


def _day_return_blocks(
    trades: tuple[PaperTrade, ...],
    returns: tuple[float, ...],
) -> tuple[tuple[float, ...], ...]:
    grouped: dict[dt.date, list[float]] = {}
    for trade, value in zip(trades, returns, strict=True):
        session_date = trade.exit_at.astimezone(NEW_YORK).date()
        grouped.setdefault(session_date, []).append(value)
    return tuple(tuple(values) for values in grouped.values())


def day_block_bootstrap_interval(
    blocks: tuple[tuple[float, ...], ...],
    bootstrap_samples: int,
    random_seed: int,
) -> tuple[float | None, float | None]:
    if len(blocks) < 2 or bootstrap_samples <= 0:
        return None, None
    rng = random.Random(random_seed)
    means: list[float] = []
    for _ in range(bootstrap_samples):
        selected = tuple(rng.choice(blocks) for _ in blocks)
        sample = tuple(value for block in selected for value in block)
        means.append(sum(sample) / len(sample))
    ordered = sorted(means)
    return (
        ordered[int((len(ordered) - 1) * 0.025)],
        ordered[int((len(ordered) - 1) * 0.975)],
    )
