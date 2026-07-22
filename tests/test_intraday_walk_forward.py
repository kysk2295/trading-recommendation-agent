from __future__ import annotations

from pathlib import Path

import trading_agent.challenger_replay_runner as runner
import trading_agent.intraday_research_loop_models as models
from trading_agent.replay import load_bounded_bars
from trading_agent.strategy_factory import StrategyMode

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE = PROJECT / "examples" / "example_intraday.csv"


def test_gap_challenger_runs_one_cost_adjusted_walk_forward_fold(tmp_path: Path) -> None:
    # Given: fixed parameters and the current seven-bar intraday example.
    request = models.IntradayWalkForwardRequest(
        bars=load_bounded_bars(EXAMPLE, max_rows=10, max_sessions=1),
        strategy=StrategyMode.GAP_AND_GO,
        minimum_training_sessions=0,
        per_side_cost_bps=20,
        bootstrap_samples=200,
        rss_limit_gib=9.5,
    )

    # When: a rolling-origin historical experiment is run sequentially.
    result = runner.run_intraday_walk_forward(request, tmp_path)

    # Then: only the OOS fold contributes one cost-adjusted completed trade.
    assert result.strategy is StrategyMode.GAP_AND_GO
    assert result.observed_sessions == 1
    assert result.fold_count == 1
    assert result.trade_count == 1
    assert result.side_cost_bps == 20
    assert result.average_return is not None
    assert result.gross_average_return is not None
    assert result.average_return < result.gross_average_return
    assert result.peak_rss_gib < 9.5


def test_walk_forward_requires_an_oos_session_after_training(tmp_path: Path) -> None:
    # Given: the only session is reserved for training.
    request = models.IntradayWalkForwardRequest(
        bars=load_bounded_bars(EXAMPLE, max_rows=10, max_sessions=1),
        strategy=StrategyMode.GAP_AND_GO,
        minimum_training_sessions=1,
        per_side_cost_bps=20,
        bootstrap_samples=200,
        rss_limit_gib=9.5,
    )

    # When/Then: no in-sample trade is relabeled as OOS evidence.
    try:
        _ = runner.run_intraday_walk_forward(request, tmp_path)
    except models.IntradayWalkForwardError as error:
        assert error.reason == "no_oos_sessions"
    else:
        raise AssertionError("expected no_oos_sessions")
