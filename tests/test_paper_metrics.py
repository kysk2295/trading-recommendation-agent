from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent import metrics
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.store import PaperStore


def test_trade_extraction_requires_activation_and_a_terminal_exit(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    entered = _recommendation("entered", "WIN", 10.0)
    store.save(entered)
    store.set_state(
        entered.recommendation_id,
        RecommendationState.ACTIVE,
        entered.created_at + dt.timedelta(minutes=1),
        entered.entry,
        "조건부 진입가 도달",
    )
    store.set_state(
        entered.recommendation_id,
        RecommendationState.TARGET_2R,
        entered.created_at + dt.timedelta(minutes=2),
        11.0,
        "2R 목표가 도달",
    )
    invalidated = _recommendation("invalid", "NOFILL", 20.0)
    store.save(invalidated)
    store.set_state(
        invalidated.recommendation_id,
        RecommendationState.INVALIDATED,
        invalidated.created_at + dt.timedelta(minutes=1),
        19.5,
        "진입 전 무효",
    )
    unresolved = _recommendation("open", "OPEN", 30.0)
    store.save(unresolved)

    trades = metrics.extract_paper_trades((store,))

    assert len(trades) == 1
    assert trades[0].recommendation_id == "entered"
    assert trades[0].entry == 10.0
    assert trades[0].exit == 11.0
    assert trades[0].gross_return == 0.1
    assert trades[0].exit_state is RecommendationState.TARGET_2R
    assert not trades[0].uses_close_fallback


def test_performance_metrics_apply_round_trip_costs_and_track_fallbacks() -> None:
    timestamp = dt.datetime(
        2026, 7, 10, 10, 0, tzinfo=ZoneInfo("America/New_York")
    )
    trades = (
        metrics.PaperTrade(
            "winner",
            "WIN",
            "opening_range_breakout",
            timestamp,
            timestamp + dt.timedelta(minutes=1),
            10.0,
            11.0,
            0.1,
            RecommendationState.TARGET_2R,
            False,
        ),
        metrics.PaperTrade(
            "loser",
            "LOSS",
            "opening_range_breakout",
            timestamp + dt.timedelta(minutes=2),
            timestamp + dt.timedelta(minutes=3),
            10.0,
            9.5,
            -0.05,
            RecommendationState.TIME_EXIT,
            True,
        ),
    )

    result = metrics.summarize_performance(
        trades,
        metrics.MetricsConfig(10, 200, 7),
    )

    winner_net = 11.0 * 0.999 / (10.0 * 1.001) - 1.0
    loser_net = 9.5 * 0.999 / (10.0 * 1.001) - 1.0
    assert result.side_cost_bps == 10
    assert result.trade_count == 2
    assert result.win_count == 1
    assert result.win_rate == 0.5
    assert result.average_return == (winner_net + loser_net) / 2.0
    assert result.profit_factor == winner_net / abs(loser_net)
    assert result.cumulative_return == (1.0 + winner_net) * (1.0 + loser_net) - 1.0
    assert result.max_drawdown == loser_net
    assert result.fallback_exit_count == 1
    assert result.fallback_exit_rate == 0.5
    mean = result.average_return
    lower = result.mean_ci_low
    upper = result.mean_ci_high
    assert mean is not None
    assert lower is not None
    assert upper is not None
    assert lower <= mean <= upper


def _recommendation(
    recommendation_id: str,
    symbol: str,
    entry: float,
) -> Recommendation:
    created_at = dt.datetime(
        2026, 7, 10, 10, 0, tzinfo=ZoneInfo("America/New_York")
    )
    return Recommendation(
        recommendation_id,
        symbol,
        "opening_range_breakout",
        created_at,
        entry,
        entry - 0.5,
        entry + 0.5,
        entry + 1.0,
        RecommendationState.SETUP,
        "metrics fixture",
    )
