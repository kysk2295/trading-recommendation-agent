from __future__ import annotations

from collections.abc import Iterable
from typing import Final, Literal

from trading_agent.adaptive_evaluation_models import (
    AdaptiveAction,
    AdaptiveEvaluation,
    EvaluatedSession,
    EvaluationContext,
    RegimeEvidence,
    WindowEvidence,
)
from trading_agent.metrics import MetricsConfig, PerformanceMetrics, summarize_performance

WINDOWS: Final[tuple[Literal[5, 10, 20, 60], ...]] = (5, 10, 20, 60)
BOOTSTRAP_SAMPLES: Final = 2_000
BOOTSTRAP_SEED: Final = 20_260_715


def evaluate_strategy(
    sessions: tuple[EvaluatedSession, ...],
    context: EvaluationContext,
) -> AdaptiveEvaluation:
    ordered = tuple(sorted(sessions, key=lambda row: row.session_date))
    windows = tuple(_window(ordered, required) for required in WINDOWS)
    five, ten, twenty, sixty = windows
    recent = ordered[-60:]
    regimes = _regime_evidence(recent)
    regime_coverage = 0.0 if not recent else sum(row.regime is not None for row in recent) / len(recent)
    research_blockers = _proof_blockers(sixty, regime_coverage, regimes)
    proof_blockers = _unique((*research_blockers, *context.external_promotion_blockers))
    action, reasons = _action(ordered, five, ten, twenty, research_blockers)
    return AdaptiveEvaluation(
        schema_version=1,
        as_of=context.as_of,
        strategy_version=context.strategy_version,
        evaluator_version=context.evaluator_version,
        action=action,
        reasons=reasons,
        windows=windows,
        regime_coverage=regime_coverage,
        regimes=regimes,
        proof_blockers=proof_blockers,
        automatic_state_change_allowed=False,
    )


def _window(
    sessions: tuple[EvaluatedSession, ...],
    required: Literal[5, 10, 20, 60],
) -> WindowEvidence:
    selected = sessions[-required:]
    metrics = summarize_performance(
        tuple(trade for session in selected for trade in session.trades),
        MetricsConfig(20, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED + required),
    )
    return _window_evidence(required, len(selected), len(sessions) >= required, metrics)


def _window_evidence(
    required: Literal[5, 10, 20, 60],
    observed: int,
    complete: bool,
    metrics: PerformanceMetrics,
) -> WindowEvidence:
    return WindowEvidence(
        required_sessions=required,
        observed_sessions=observed,
        complete=complete,
        trade_count=metrics.trade_count,
        win_rate=metrics.win_rate,
        average_return=metrics.average_return,
        profit_factor=metrics.profit_factor,
        cumulative_return=metrics.cumulative_return,
        max_drawdown=metrics.max_drawdown,
        mean_ci_low=metrics.mean_ci_low,
        mean_ci_high=metrics.mean_ci_high,
    )


def _regime_evidence(sessions: tuple[EvaluatedSession, ...]) -> tuple[RegimeEvidence, ...]:
    labels = tuple(sorted({row.regime for row in sessions if row.regime is not None}))
    evidence: list[RegimeEvidence] = []
    for label in labels:
        selected = tuple(row for row in sessions if row.regime == label)
        metrics = summarize_performance(
            tuple(trade for session in selected for trade in session.trades),
            MetricsConfig(20, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED),
        )
        evidence.append(
            RegimeEvidence(
                regime=label,
                session_count=len(selected),
                trade_count=metrics.trade_count,
                average_return=metrics.average_return,
                profit_factor=metrics.profit_factor,
                mean_ci_low=metrics.mean_ci_low,
                mean_ci_high=metrics.mean_ci_high,
            )
        )
    return tuple(evidence)


def _proof_blockers(
    sixty: WindowEvidence,
    regime_coverage: float,
    regimes: tuple[RegimeEvidence, ...],
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not sixty.complete:
        blockers.append(f"minimum_forward_days:{sixty.observed_sessions}/60")
    if sixty.trade_count < 100:
        blockers.append(f"minimum_completed_trades:{sixty.trade_count}/100")
    if sixty.profit_factor is None or sixty.profit_factor < 1.15:
        blockers.append("rolling_60_pf_below_1.15")
    if sixty.average_return is None or sixty.average_return <= 0.0:
        blockers.append("rolling_60_average_nonpositive")
    if sixty.mean_ci_low is None or sixty.mean_ci_low < 0.0:
        blockers.append("rolling_60_ci_lower_below_zero")
    if regime_coverage < 0.8:
        blockers.append("regime_coverage_below_80pct")
    if len(regimes) < 2:
        blockers.append("regime_diversity_below_2")
    blockers.extend(
        f"regime_instability:{row.regime}"
        for row in regimes
        if row.trade_count >= 10
        and (
            row.profit_factor is None
            or row.profit_factor < 0.8
            or row.average_return is None
            or row.average_return <= 0.0
        )
    )
    return tuple(blockers)


def _action(
    sessions: tuple[EvaluatedSession, ...],
    five: WindowEvidence,
    ten: WindowEvidence,
    twenty: WindowEvidence,
    research_blockers: tuple[str, ...],
) -> tuple[AdaptiveAction, tuple[str, ...]]:
    if _clear_failure(five):
        if len(sessions) >= 20:
            return AdaptiveAction.SUSPEND, ("five_day_clear_degradation",)
        return AdaptiveAction.EARLY_STOP, ("five_day_clear_failure",)
    if _weak_edge(ten):
        return AdaptiveAction.DIAGNOSE, ("ten_day_edge_weak",)
    if not research_blockers:
        return AdaptiveAction.PROMOTION_REVIEW, ("sixty_day_proof_ready",)
    if _positive_edge(twenty, 30):
        return AdaptiveAction.COMPARISON_READY, ("twenty_day_shadow_edge",)
    if five.complete:
        return AdaptiveAction.SHADOW_CONTINUE, ("five_day_hard_stop_not_triggered",)
    return AdaptiveAction.COLLECTING, ("minimum_five_day_observation_pending",)


def _clear_failure(window: WindowEvidence) -> bool:
    return (
        window.complete
        and window.trade_count >= 10
        and window.profit_factor is not None
        and window.profit_factor < 0.75
        and window.average_return is not None
        and window.average_return < 0.0
        and window.mean_ci_high is not None
        and window.mean_ci_high < 0.0
    )


def _weak_edge(window: WindowEvidence) -> bool:
    return (
        window.complete
        and window.trade_count >= 15
        and (
            window.profit_factor is None
            or window.profit_factor < 1.0
            or window.average_return is None
            or window.average_return <= 0.0
        )
    )


def _positive_edge(window: WindowEvidence, minimum_trades: int) -> bool:
    return (
        window.complete
        and window.trade_count >= minimum_trades
        and window.profit_factor is not None
        and window.profit_factor >= 1.15
        and window.average_return is not None
        and window.average_return > 0.0
        and window.mean_ci_low is not None
        and window.mean_ci_low >= 0.0
    )


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
