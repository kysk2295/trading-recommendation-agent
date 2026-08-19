from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from trading_agent.strategy_research_types import ExpectedDirection, ResearchAgentId


class ResamplingMethod(StrEnum):
    SESSION_MOVING_BLOCK = "session_moving_block"
    EVENT_CLUSTER = "event_cluster"
    DATE_CLUSTER = "date_cluster"
    UNDERLYING_MATURITY_CLUSTER = "underlying_maturity_cluster"


@dataclass(frozen=True, slots=True)
class StrategyResearchMethodology:
    agent_id: ResearchAgentId
    accepted_source_authorities: tuple[str, ...]
    required_source_authorities: tuple[str, ...]
    freshness_by_authority: tuple[tuple[str, dt.timedelta], ...]
    observation_grammar: str
    predictor_grammar: str
    target_formula: str
    target_horizon: dt.timedelta
    expected_direction: ExpectedDirection
    cadence_key: str
    maturity_rule: str
    resampling_method: ResamplingMethod
    cost_model_id: str
    baseline_id: str
    next_test_policy: str
    entry_rule: str
    exit_rule: str
    stop_rule: str


_METHODOLOGIES: Final = {
    ResearchAgentId.INTRADAY_MOMENTUM: StrategyResearchMethodology(
        agent_id=ResearchAgentId.INTRADAY_MOMENTUM,
        accepted_source_authorities=(
            "consolidated_completed_bar",
            "fresh_actionable_spread",
            "current_market_session",
        ),
        required_source_authorities=(
            "consolidated_completed_bar",
            "fresh_actionable_spread",
            "current_market_session",
        ),
        freshness_by_authority=(
            ("consolidated_completed_bar", dt.timedelta(minutes=10)),
            ("fresh_actionable_spread", dt.timedelta(minutes=2)),
            ("current_market_session", dt.timedelta(minutes=1)),
        ),
        observation_grammar="latest completed bar continuation with fresh spread in current NY session",
        predictor_grammar="completed-bar continuation rank known strictly after bar close",
        target_formula="same-session net excess return over six completed bars",
        target_horizon=dt.timedelta(minutes=30),
        expected_direction=ExpectedDirection.POSITIVE,
        cadence_key="each_eligible_five_minute_bar_plus_five_minutes",
        maturity_rule="six subsequent completed bars or session close",
        resampling_method=ResamplingMethod.SESSION_MOVING_BLOCK,
        cost_model_id="intraday-spread-impact-v1",
        baseline_id="timestamp-session-matched-continuation-null-v1",
        next_test_policy="future-session continuation replication with spread sensitivity",
        entry_rule="enter only after source bar completion at the next eligible quote",
        exit_rule="exit after six completed bars or session close",
        stop_rule="preregistered adverse boundary; stop wins same-bar collision",
    ),
    ResearchAgentId.INTRADAY_MEAN_REVERSION: StrategyResearchMethodology(
        agent_id=ResearchAgentId.INTRADAY_MEAN_REVERSION,
        accepted_source_authorities=(
            "residual_dislocation_snapshot",
            "fresh_reversion_spread",
            "current_market_session",
        ),
        required_source_authorities=(
            "residual_dislocation_snapshot",
            "fresh_reversion_spread",
            "current_market_session",
        ),
        freshness_by_authority=(
            ("residual_dislocation_snapshot", dt.timedelta(minutes=10)),
            ("fresh_reversion_spread", dt.timedelta(minutes=2)),
            ("current_market_session", dt.timedelta(minutes=1)),
        ),
        observation_grammar="completed-bar residual dislocation with actionable reversion spread",
        predictor_grammar="signed residual displacement rank after displacement maturity",
        target_formula="same-session residual normalization return net of costs",
        target_horizon=dt.timedelta(minutes=30),
        expected_direction=ExpectedDirection.NEGATIVE,
        cadence_key="each_mature_five_minute_dislocation_plus_five_minutes",
        maturity_rule="normalization, six completed bars, or session close",
        resampling_method=ResamplingMethod.SESSION_MOVING_BLOCK,
        cost_model_id="intraday-reversion-turnover-v1",
        baseline_id="timestamp-residual-permutation-null-v1",
        next_test_policy="future-session reversion replication with extension ablation",
        entry_rule="enter after displacement maturity with a fresh actionable spread",
        exit_rule="exit at residual normalization or bounded horizon",
        stop_rule="preregistered extension boundary; stop wins same-bar collision",
    ),
    ResearchAgentId.CATALYST_EVENT: StrategyResearchMethodology(
        agent_id=ResearchAgentId.CATALYST_EVENT,
        accepted_source_authorities=("verified_event_receipt", "eligible_session_calendar"),
        required_source_authorities=("verified_event_receipt", "eligible_session_calendar"),
        freshness_by_authority=(
            ("verified_event_receipt", dt.timedelta(days=2)),
            ("eligible_session_calendar", dt.timedelta(days=1)),
        ),
        observation_grammar="immutable verified disclosure or qualified-news event receipt",
        predictor_grammar="point-in-time preregistered catalyst surprise rank",
        target_formula="censored two-session post-event excess return net of costs",
        target_horizon=dt.timedelta(days=2),
        expected_direction=ExpectedDirection.POSITIVE,
        cadence_key="novel_event_receipt_plus_fifteen_minutes",
        maturity_rule="two eligible post-event sessions with censoring for incomplete windows",
        resampling_method=ResamplingMethod.EVENT_CLUSTER,
        cost_model_id="event-gap-liquidity-v1",
        baseline_id="event-time-industry-matched-null-v1",
        next_test_policy="future-event replication by independent event cluster",
        entry_rule="enter after event maturity gate at the next eligible open",
        exit_rule="exit at the second eligible session close",
        stop_rule="preregistered adverse event-return boundary",
    ),
    ResearchAgentId.SWING_TREND_REGIME: StrategyResearchMethodology(
        agent_id=ResearchAgentId.SWING_TREND_REGIME,
        accepted_source_authorities=("adjusted_completed_daily_bar", "ex_ante_regime_snapshot"),
        required_source_authorities=("adjusted_completed_daily_bar", "ex_ante_regime_snapshot"),
        freshness_by_authority=(
            ("adjusted_completed_daily_bar", dt.timedelta(days=2)),
            ("ex_ante_regime_snapshot", dt.timedelta(days=2)),
        ),
        observation_grammar="completed adjusted daily bar joined to an ex-ante regime snapshot",
        predictor_grammar="completed-session trend rank conditioned on prior-known regime",
        target_formula="five-session regime-conditioned excess return net of costs",
        target_horizon=dt.timedelta(days=5),
        expected_direction=ExpectedDirection.POSITIVE,
        cadence_key="completed_nyse_session_plus_thirty_minutes",
        maturity_rule="five subsequent eligible sessions with overlap-safe labels",
        resampling_method=ResamplingMethod.SESSION_MOVING_BLOCK,
        cost_model_id="multi-session-turnover-v1",
        baseline_id="regime-matched-market-style-null-v1",
        next_test_policy="future-regime replication across a non-overlapping session block",
        entry_rule="enter no earlier than the next session after source completion",
        exit_rule="exit after five eligible sessions",
        stop_rule="preregistered multi-session adverse-return boundary",
    ),
    ResearchAgentId.CROSS_SECTIONAL_QUANT: StrategyResearchMethodology(
        agent_id=ResearchAgentId.CROSS_SECTIONAL_QUANT,
        accepted_source_authorities=("pit_universe_membership", "same_timestamp_factor_ranking"),
        required_source_authorities=("pit_universe_membership", "same_timestamp_factor_ranking"),
        freshness_by_authority=(
            ("pit_universe_membership", dt.timedelta(days=2)),
            ("same_timestamp_factor_ranking", dt.timedelta(days=2)),
        ),
        observation_grammar="point-in-time membership and same-timestamp cross-sectional ranking",
        predictor_grammar="sector-neutral rank computed only within the PIT eligible universe",
        target_formula="five-session top-minus-bottom spread net of turnover costs",
        target_horizon=dt.timedelta(days=5),
        expected_direction=ExpectedDirection.TWO_SIDED,
        cadence_key="mature_session_snapshot_plus_forty_five_minutes",
        maturity_rule="next preregistered rank date after five eligible sessions",
        resampling_method=ResamplingMethod.DATE_CLUSTER,
        cost_model_id="cross-sectional-turnover-capacity-v1",
        baseline_id="same-date-sector-neutral-rank-null-v1",
        next_test_policy="future-date replication with factor and membership ablation",
        entry_rule="form only after membership and rank snapshot maturity",
        exit_rule="rebalance at the next preregistered rank maturity",
        stop_rule="invalidate when neutrality, membership, or turnover bounds fail",
    ),
    ResearchAgentId.DERIVATIVES_VOLATILITY: StrategyResearchMethodology(
        agent_id=ResearchAgentId.DERIVATIVES_VOLATILITY,
        accepted_source_authorities=("official_option_surface", "spot_hedge_convention"),
        required_source_authorities=("official_option_surface", "spot_hedge_convention"),
        freshness_by_authority=(
            ("official_option_surface", dt.timedelta(days=1)),
            ("spot_hedge_convention", dt.timedelta(days=1)),
        ),
        observation_grammar="complete official term/skew surface with maturity-matched spot hedge",
        predictor_grammar="maturity-matched implied-realized, term, and skew spread",
        target_formula="hedged maturity-matched net volatility carry",
        target_horizon=dt.timedelta(days=5),
        expected_direction=ExpectedDirection.TWO_SIDED,
        cadence_key="completed_derivatives_session_boundary",
        maturity_rule="matched underlying and option maturity observation window",
        resampling_method=ResamplingMethod.UNDERLYING_MATURITY_CLUSTER,
        cost_model_id="option-surface-hedge-slippage-v1",
        baseline_id="spot-only-maturity-matched-null-v1",
        next_test_policy="future underlying-maturity replication against spot-only baseline",
        entry_rule="enter only after surface and hedge quote authority are complete",
        exit_rule="exit at the preregistered maturity boundary",
        stop_rule="stop when surface, hedge coverage, or quote authority is incomplete",
    ),
}


def strategy_research_methodology(agent_id: ResearchAgentId) -> StrategyResearchMethodology:
    return _METHODOLOGIES[agent_id]


__all__ = (
    "ResamplingMethod",
    "StrategyResearchMethodology",
    "strategy_research_methodology",
)
