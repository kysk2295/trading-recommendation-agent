from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import pytest

import trading_agent.challenger_replay_runner as runner
import trading_agent.intraday_research_loop_models as models
from trading_agent.intraday_research_artifacts import (
    IntradayExperimentPayload,
    intraday_experiment_artifact,
)
from trading_agent.metrics import net_return
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
    assert result.schema_version == 2
    assert result.bootstrap_samples == 200
    assert result.bootstrap_seed == 20_260_722
    assert len(result.session_outcomes) == 1
    outcome = result.session_outcomes[0]
    assert outcome.session_date == dt.date(2026, 1, 2)
    assert outcome.trade_count == 1
    assert len(outcome.gross_trade_returns) == 1
    assert len(outcome.net_trade_returns) == 1
    assert math.isclose(outcome.gross_trade_returns[0], result.gross_average_return)
    trade = runner.extract_paper_trades((runner.PaperStore(tmp_path / "gap_and_go.sqlite3"),))[0]
    assert math.isclose(outcome.net_trade_returns[0], net_return(trade, 20))
    assert math.isclose(outcome.net_trade_returns[0], result.average_return)


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


def test_walk_forward_trace_rejects_tampering_and_preserves_legacy_v1(
    tmp_path: Path,
) -> None:
    request = models.IntradayWalkForwardRequest(
        bars=load_bounded_bars(EXAMPLE, max_rows=10, max_sessions=1),
        strategy=StrategyMode.GAP_AND_GO,
        minimum_training_sessions=0,
        per_side_cost_bps=20,
        bootstrap_samples=200,
        rss_limit_gib=9.5,
    )
    result = runner.run_intraday_walk_forward(request, tmp_path)
    tampered = result.model_dump(mode="json")
    tampered["session_outcomes"][0]["net_trade_returns"][0] = 0.5

    with pytest.raises(ValueError, match="invalid intraday session outcome"):
        _ = models.IntradayWalkForwardResult.model_validate(tampered)

    invalid_interval = result.model_dump(mode="json")
    invalid_interval["mean_ci_low"] = 0.0
    with pytest.raises(ValueError, match="invalid intraday walk-forward outcome trace"):
        _ = models.IntradayWalkForwardResult.model_validate(invalid_interval)

    invalid_cost = result.model_dump(mode="json")
    wrong_net = invalid_cost["session_outcomes"][0]["gross_trade_returns"][0] - 0.01
    invalid_cost["session_outcomes"][0]["net_trade_returns"][0] = wrong_net
    invalid_cost["average_return"] = wrong_net
    invalid_cost["cumulative_return"] = wrong_net
    with pytest.raises(ValueError, match="invalid intraday walk-forward outcome trace"):
        _ = models.IntradayWalkForwardResult.model_validate(invalid_cost)

    legacy_raw = result.model_dump(mode="json")
    legacy_raw["schema_version"] = 1
    del legacy_raw["bootstrap_samples"]
    del legacy_raw["bootstrap_seed"]
    del legacy_raw["session_outcomes"]
    legacy = models.IntradayWalkForwardResult.model_validate(legacy_raw)
    payload = IntradayExperimentPayload(
        schema_version=1,
        trial_id="legacy-trial",
        strategy_version="legacy-strategy-v1",
        evaluator_version="intraday_walk_forward_v1",
        data_version="a" * 64,
        manifest_sha256="b" * 64,
        registered_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        started_at=dt.datetime(2026, 1, 1, 0, 0, 1, tzinfo=dt.UTC),
        completed_at=dt.datetime(2026, 1, 1, 0, 0, 2, tzinfo=dt.UTC),
        result=legacy,
    )

    artifact = intraday_experiment_artifact(payload)

    assert legacy.bootstrap_samples is None
    assert legacy.session_outcomes == ()
    assert artifact.schema_version == 1
    assert artifact.payload.schema_version == 1


def test_legacy_evaluator_keeps_aggregate_only_schema_v1(tmp_path: Path) -> None:
    request = models.IntradayWalkForwardRequest(
        bars=load_bounded_bars(EXAMPLE, max_rows=10, max_sessions=1),
        strategy=StrategyMode.GAP_AND_GO,
        minimum_training_sessions=0,
        per_side_cost_bps=20,
        bootstrap_samples=200,
        rss_limit_gib=9.5,
        evaluator_version="intraday_walk_forward_v1",
    )

    result = runner.run_intraday_walk_forward(request, tmp_path)

    assert result.schema_version == 1
    assert result.bootstrap_samples is None
    assert result.bootstrap_seed is None
    assert result.session_outcomes == ()
