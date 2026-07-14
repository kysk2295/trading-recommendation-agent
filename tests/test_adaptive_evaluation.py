from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from zoneinfo import ZoneInfo

from trading_agent.adaptive_evaluation import evaluate_strategy
from trading_agent.adaptive_evaluation_models import (
    AdaptiveAction,
    EvaluatedSession,
    EvaluationContext,
)
from trading_agent.metrics import PaperTrade
from trading_agent.models import RecommendationState
from trading_agent.trade_cohort_models import (
    DollarVolumeBucket,
    FeatureStatus,
    GapBucket,
    PriceBucket,
    TradeFeatureAssignment,
    VolumeToAdvBucket,
)

NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class _DayPattern:
    returns: tuple[float, ...]
    regime: str | None


def test_evaluation_reports_collecting_when_no_day_is_quality_eligible() -> None:
    # Given: the current session is recorded but every observed day is censored by quality gates.
    sessions: tuple[EvaluatedSession, ...] = ()

    # When: the adaptive evaluator runs for the current as-of date.
    result = evaluate_strategy(sessions, _context())

    # Then: it records zero evidence without turning missing data into a failed trade.
    assert result.action is AdaptiveAction.COLLECTING
    assert result.windows[0].observed_sessions == 0
    assert result.windows[0].trade_count == 0


def test_evaluation_collects_before_five_eligible_days() -> None:
    # Given: four eligible sessions with positive shadow trades.
    sessions = _sessions(4, _DayPattern((0.03, 0.03, -0.01), "risk_on"))

    # When: the adaptive evidence gate evaluates the strategy.
    result = evaluate_strategy(sessions, _context())

    # Then: it keeps collecting without claiming that twelve weeks are required.
    assert result.action is AdaptiveAction.COLLECTING
    assert result.windows[0].required_sessions == 5
    assert result.windows[0].observed_sessions == 4
    assert result.windows[0].complete is False
    assert result.automatic_state_change_allowed is False


def test_evaluation_stops_clear_failure_after_five_days_and_ten_trades() -> None:
    # Given: five complete days whose 20bp returns are consistently negative.
    sessions = _sessions(5, _DayPattern((-0.02, -0.01), "risk_off"))

    # When: the five-day evidence gate runs.
    result = evaluate_strategy(sessions, _context())

    # Then: the strategy is recommended for early stop, not held for sixty days.
    assert result.action is AdaptiveAction.EARLY_STOP
    assert "five_day_clear_failure" in result.reasons
    assert result.windows[0].trade_count == 10
    assert result.windows[0].mean_ci_high is not None
    assert result.windows[0].mean_ci_high < 0.0


def test_evaluation_diagnoses_weak_ten_day_record_without_false_five_day_stop() -> None:
    # Given: an early weak block followed by five mixed days that avoid the hard-stop rule.
    weak = _sessions(5, _DayPattern((-0.02, -0.01, 0.005), "risk_off"))
    mixed = _sessions(
        5,
        _DayPattern((0.02, -0.01, 0.0), "risk_on"),
        start=dt.date(2026, 1, 6),
    )

    # When: ten eligible days are evaluated.
    result = evaluate_strategy((*weak, *mixed), _context())

    # Then: the strategy moves to diagnosis rather than automatic suspension.
    assert result.action is AdaptiveAction.DIAGNOSE
    assert "ten_day_edge_weak" in result.reasons


def test_evaluation_marks_twenty_day_stable_shadow_for_comparison() -> None:
    # Given: twenty complete days with a stable positive edge after 20bp costs.
    sessions = _sessions(20, _DayPattern((0.03, 0.03, -0.01), "risk_on"))

    # When: rolling evidence is evaluated.
    result = evaluate_strategy(sessions, _context())

    # Then: it is ready for equal-risk comparison, not promoted to trading.
    assert result.action is AdaptiveAction.COMPARISON_READY
    assert "twenty_day_shadow_edge" in result.reasons
    assert result.automatic_state_change_allowed is False


def test_recent_ten_day_weakness_overrides_positive_twenty_day_aggregate() -> None:
    # Given: ten very strong days followed by a weak ten-day block whose last five avoid the hard stop.
    strong = _sessions(10, _DayPattern((0.10, 0.10, -0.01), "risk_on"))
    weak = _sessions(
        5,
        _DayPattern((-0.02, -0.01, 0.005), "risk_off"),
        start=dt.date(2026, 1, 11),
    )
    mixed = _sessions(
        5,
        _DayPattern((0.02, -0.01, 0.0), "risk_on"),
        start=dt.date(2026, 1, 16),
    )

    # When: the rolling windows disagree about the current edge.
    result = evaluate_strategy((*strong, *weak, *mixed), _context())

    # Then: recent deterioration is diagnosed before aggregate comparison readiness.
    assert result.windows[2].mean_ci_low is not None
    assert result.windows[2].mean_ci_low >= 0.0
    assert result.action is AdaptiveAction.DIAGNOSE


def test_evaluation_suspends_mature_candidate_on_recent_clear_degradation() -> None:
    # Given: a twenty-day stable candidate followed by five uniformly losing days.
    stable = _sessions(20, _DayPattern((0.03, 0.03, -0.01), "risk_on"))
    degraded = _sessions(
        5,
        _DayPattern((-0.02, -0.01), "risk_off"),
        start=dt.date(2026, 1, 21),
    )

    # When: the latest five-day window is evaluated.
    result = evaluate_strategy((*stable, *degraded), _context())

    # Then: the recommendation is immediate shadow suspension.
    assert result.action is AdaptiveAction.SUSPEND
    assert "five_day_clear_degradation" in result.reasons


def test_evaluation_uses_sixty_days_only_for_final_review_eligibility() -> None:
    # Given: sixty strong days, 180 trades, and two pre-classified regimes.
    sessions = tuple(
        _session(
            dt.date(2026, 1, 1) + dt.timedelta(days=index),
            _DayPattern((0.03, 0.03, -0.01), "risk_on" if index % 2 == 0 else "risk_off"),
        )
        for index in range(60)
    )

    # When: the final evidence window is evaluated with external safety blockers.
    result = evaluate_strategy(sessions, _context(("broker_paper_ledger_missing",)))

    # Then: it requests a promotion review but cannot change state automatically.
    assert result.action is AdaptiveAction.PROMOTION_REVIEW
    assert result.proof_blockers == ("broker_paper_ledger_missing",)
    assert result.regime_coverage == 1.0
    assert len(result.regimes) == 2
    assert result.automatic_state_change_allowed is False


def test_evaluation_segments_returns_by_point_in_time_stock_features() -> None:
    # Given: five eligible days whose every trade has causal price, gap, volume, and liquidity features.
    sessions = _sessions(5, _DayPattern((0.03, 0.03, -0.01), "risk_on"))

    # When: adaptive evidence is calculated.
    result = evaluate_strategy(sessions, _context())

    # Then: feature coverage is complete and all four dimensions have independent cohort metrics.
    assert result.feature_coverage == 1.0
    assert result.gap_feature_coverage == 1.0
    assert {row.dimension for row in result.cohorts} == {
        "price",
        "opening_gap",
        "volume_to_adv",
        "dollar_volume",
    }


def test_evaluation_blocks_final_review_when_gap_feature_coverage_is_missing() -> None:
    # Given: sixty otherwise strong days whose gap snapshot is unavailable at decision time.
    sessions = _sessions(60, _DayPattern((0.03, 0.03, -0.01), "risk_on"))
    without_gap = tuple(
        replace(
            session,
            features=tuple(replace(feature, opening_gap_pct=None, gap_bucket=None) for feature in session.features),
        )
        for session in sessions
    )

    # When: final review evidence is evaluated.
    result = evaluate_strategy(without_gap, _context())

    # Then: aggregate profitability cannot hide missing stock-feature coverage.
    assert result.gap_feature_coverage == 0.0
    assert "gap_feature_coverage_below_80pct" in result.proof_blockers
    assert result.action is not AdaptiveAction.PROMOTION_REVIEW


def test_evaluation_flags_losing_price_cohort_hidden_by_positive_aggregate() -> None:
    # Given: strong aggregate returns whose losing leg is isolated in a separate price bucket.
    sessions = _sessions(60, _DayPattern((0.03, 0.03, -0.01), "risk_on"))
    segmented = tuple(
        replace(
            session,
            features=tuple(
                replace(
                    feature,
                    price=3.0,
                    price_bucket=PriceBucket.LT_5,
                )
                if index == 2
                else feature
                for index, feature in enumerate(session.features)
            ),
        )
        for session in sessions
    )

    # When: cohort evidence is evaluated.
    result = evaluate_strategy(segmented, _context())

    # Then: the low-price weakness remains a final-review blocker.
    assert "cohort_instability:price:price_lt_5" in result.proof_blockers


def _context(blockers: tuple[str, ...] = ()) -> EvaluationContext:
    return EvaluationContext(
        as_of=dt.date(2026, 3, 1),
        strategy_version="orb-test-v1",
        evaluator_version="paper_metrics_day_block_bootstrap_v2",
        external_promotion_blockers=blockers,
    )


def _sessions(
    count: int,
    pattern: _DayPattern,
    *,
    start: dt.date = dt.date(2026, 1, 1),
) -> tuple[EvaluatedSession, ...]:
    return tuple(_session(start + dt.timedelta(days=index), pattern) for index in range(count))


def _session(session_date: dt.date, pattern: _DayPattern) -> EvaluatedSession:
    trades = tuple(_trade(session_date, index, value) for index, value in enumerate(pattern.returns))
    features = tuple(_feature(trade) for trade in trades)
    return EvaluatedSession(session_date, trades, pattern.regime, features)


def _trade(session_date: dt.date, index: int, gross_return: float) -> PaperTrade:
    entered_at = dt.datetime.combine(session_date, dt.time(10), NEW_YORK) + dt.timedelta(minutes=index)
    return PaperTrade(
        recommendation_id=f"{session_date}-{index}",
        symbol=f"T{index}",
        strategy="opening_range_breakout",
        entry_at=entered_at,
        exit_at=entered_at + dt.timedelta(minutes=5),
        entry=100.0,
        exit=100.0 * (1.0 + gross_return),
        gross_return=gross_return,
        exit_state=RecommendationState.TIME_EXIT,
        uses_close_fallback=False,
    )


def _feature(trade: PaperTrade) -> TradeFeatureAssignment:
    return TradeFeatureAssignment(
        recommendation_id=trade.recommendation_id,
        symbol=trade.symbol,
        decision_at=trade.entry_at,
        status=FeatureStatus.COMPLETE,
        reason="",
        exchange="NAS",
        candidate_observed_at=trade.entry_at,
        risk_observed_at=trade.entry_at,
        gap_observed_at=trade.entry_at,
        price=8.0,
        change_pct=0.12,
        opening_gap_pct=0.08,
        volume_to_adv=0.30,
        dollar_volume=2_000_000.0,
        spread_bps=25.0,
        price_bucket=PriceBucket.FROM_5_TO_20,
        gap_bucket=GapBucket.FROM_4_TO_10_PCT,
        volume_to_adv_bucket=VolumeToAdvBucket.FROM_25_TO_50_PCT,
        dollar_volume_bucket=DollarVolumeBucket.FROM_1_TO_5M,
    )
