from __future__ import annotations

import datetime as dt
from pathlib import Path

from trading_agent.challenger_replay_runner import run_intraday_walk_forward
from trading_agent.daily_research_contract import strategy_contract
from trading_agent.intraday_parameter_plateau_artifacts import (
    aggregate_parameter_plateau_status,
)
from trading_agent.intraday_parameter_plateau_models import (
    IntradayParameterPlateauAnalysisRequest,
    IntradayParameterPlateauVariantTrace,
    calculate_intraday_parameter_plateau_analysis,
)
from trading_agent.intraday_parameter_plateau_variants import (
    IntradayParameterVariant,
    parameter_variants,
)
from trading_agent.intraday_research_loop_models import (
    IntradayWalkForwardRequest,
)
from trading_agent.replay import load_bounded_bars
from trading_agent.strategy_factory import StrategyMode

PROJECT = Path(__file__).resolve().parents[1]
EXAMPLE = PROJECT / "examples" / "example_intraday.csv"


def test_parameter_plateau_is_ready_when_adjacent_edges_stay_positive() -> None:
    variants = parameter_variants(StrategyMode.VWAP_RECLAIM)
    traces = tuple(
        _trace(variant, 0.01)
        for variant in variants
    )

    analysis = calculate_intraday_parameter_plateau_analysis(
        IntradayParameterPlateauAnalysisRequest(
            strategy=StrategyMode.VWAP_RECLAIM,
            trial_id="trial-vwap",
            strategy_version="vwap-source-v1",
            experiment_artifact_id="a" * 64,
            registered_parameter_set=strategy_contract(
                StrategyMode.VWAP_RECLAIM
            ).parameter_set,
            variants=traces,
        )
    )

    assert analysis.status.value == "plateau_ready"
    assert analysis.blockers == ()
    assert analysis.eligible_neighbor_count == 6
    assert analysis.positive_neighbor_count == 6
    assert analysis.positive_neighbor_rate == 1.0
    assert analysis.neighbor_average_return_min == 0.01


def test_parameter_plateau_keeps_thin_neighbors_collecting() -> None:
    variants = parameter_variants(StrategyMode.HOD_BREAKOUT)
    traces = (
        _trace(variants[0], 0.01),
        *(
            _trace(variant, 0.01, trades_per_session=1)
            for variant in variants[1:]
        ),
    )

    analysis = calculate_intraday_parameter_plateau_analysis(
        IntradayParameterPlateauAnalysisRequest(
            strategy=StrategyMode.HOD_BREAKOUT,
            trial_id="trial-hod",
            strategy_version="hod-source-v1",
            experiment_artifact_id="b" * 64,
            registered_parameter_set=strategy_contract(
                StrategyMode.HOD_BREAKOUT
            ).parameter_set,
            variants=traces,
        )
    )

    assert analysis.status.value == "collecting"
    assert analysis.blockers == ("minimum_eligible_neighbors:0/4",)
    assert analysis.eligible_neighbor_count == 0


def test_parameter_plateau_reports_absent_when_mature_neighbor_reverses() -> None:
    variants = parameter_variants(StrategyMode.GAP_AND_GO)
    traces = (
        _trace(variants[0], 0.01),
        _trace(variants[1], -0.01),
        *(
            _trace(variant, 0.01)
            for variant in variants[2:]
        ),
    )

    analysis = calculate_intraday_parameter_plateau_analysis(
        IntradayParameterPlateauAnalysisRequest(
            strategy=StrategyMode.GAP_AND_GO,
            trial_id="trial-gap",
            strategy_version="gap-source-v1",
            experiment_artifact_id="c" * 64,
            registered_parameter_set=strategy_contract(
                StrategyMode.GAP_AND_GO
            ).parameter_set,
            variants=traces,
        )
    )

    assert analysis.status.value == "plateau_not_found"
    assert analysis.blockers == ()
    assert analysis.eligible_neighbor_count == 6
    assert analysis.positive_neighbor_count == 5
    assert analysis.positive_neighbor_rate == 5 / 6
    assert analysis.neighbor_average_return_min == -0.01


def test_parameter_grid_has_center_and_six_one_axis_neighbors() -> None:
    for strategy in (
        StrategyMode.VWAP_RECLAIM,
        StrategyMode.HOD_BREAKOUT,
        StrategyMode.GAP_AND_GO,
    ):
        variants = parameter_variants(strategy)
        center = _parameters(variants[0].parameter_set)

        assert len(variants) == 7
        assert variants[0].is_center
        assert len({variant.variant_id for variant in variants}) == 7
        assert len({variant.parameter_set for variant in variants}) == 7
        assert all(
            sum(
                center[name] != value
                for name, value in _parameters(
                    variant.parameter_set
                ).items()
            )
            == 1
            for variant in variants[1:]
        )


def test_walk_forward_runs_a_predeclared_adjacent_parameter_variant(
    tmp_path: Path,
) -> None:
    variant = parameter_variants(StrategyMode.GAP_AND_GO)[1]
    request = IntradayWalkForwardRequest(
        bars=load_bounded_bars(
            EXAMPLE,
            max_rows=10,
            max_sessions=1,
        ),
        strategy=StrategyMode.GAP_AND_GO,
        minimum_training_sessions=0,
        per_side_cost_bps=20,
        bootstrap_samples=200,
        rss_limit_gib=9.5,
        parameter_variant=variant,
    )

    result = run_intraday_walk_forward(request, tmp_path)

    assert result.strategy is StrategyMode.GAP_AND_GO
    assert result.observed_sessions == 1
    assert result.schema_version == 2


def test_mature_plateau_failure_is_not_masked_by_collecting_analysis() -> None:
    gap_variants = parameter_variants(StrategyMode.GAP_AND_GO)
    gap_traces = (
        _trace(gap_variants[0], 0.01),
        _trace(gap_variants[1], -0.01),
        *(_trace(variant, 0.01) for variant in gap_variants[2:]),
    )
    failed = calculate_intraday_parameter_plateau_analysis(
        IntradayParameterPlateauAnalysisRequest(
            strategy=StrategyMode.GAP_AND_GO,
            trial_id="trial-gap",
            strategy_version="gap-source-v1",
            experiment_artifact_id="c" * 64,
            registered_parameter_set=strategy_contract(
                StrategyMode.GAP_AND_GO
            ).parameter_set,
            variants=gap_traces,
        )
    )
    hod_variants = parameter_variants(StrategyMode.HOD_BREAKOUT)
    collecting = calculate_intraday_parameter_plateau_analysis(
        IntradayParameterPlateauAnalysisRequest(
            strategy=StrategyMode.HOD_BREAKOUT,
            trial_id="trial-hod",
            strategy_version="hod-source-v1",
            experiment_artifact_id="b" * 64,
            registered_parameter_set=strategy_contract(
                StrategyMode.HOD_BREAKOUT
            ).parameter_set,
            variants=tuple(
                _trace(variant, 0.01, trades_per_session=1)
                for variant in hod_variants
            ),
        )
    )

    status = aggregate_parameter_plateau_status(
        (failed, collecting)
    )

    assert status.value == "plateau_not_found"


def _trace(
    variant: IntradayParameterVariant,
    value: float,
    *,
    trades_per_session: int = 2,
) -> IntradayParameterPlateauVariantTrace:
    dates = tuple(
        dt.date(2026, 1, 2) + dt.timedelta(days=offset)
        for offset in range(20)
    )
    returns = tuple(
        (value,) * trades_per_session
        for _ in dates
    )
    return IntradayParameterPlateauVariantTrace(
        variant_id=variant.variant_id,
        parameter_set=variant.parameter_set,
        is_center=variant.is_center,
        session_dates=dates,
        net_trade_returns_by_session=returns,
        trade_count=len(dates) * trades_per_session,
        average_return=value,
    )


def _parameters(values: tuple[str, ...]) -> dict[str, str]:
    return dict(value.split("=", maxsplit=1) for value in values)
