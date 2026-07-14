from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from trading_agent.adaptive_evaluation_models import (
    CohortDimension,
    CohortEvidence,
    EvaluatedSession,
    RegimeEvidence,
)
from trading_agent.metrics import MetricsConfig, PaperTrade, summarize_performance
from trading_agent.trade_cohort_models import FeatureStatus, TradeFeatureAssignment

BOOTSTRAP_SAMPLES: Final = 2_000
BOOTSTRAP_SEED: Final = 20_260_715


@dataclass(frozen=True, slots=True)
class SegmentationEvidence:
    regime_coverage: float
    regimes: tuple[RegimeEvidence, ...]
    feature_coverage: float
    gap_feature_coverage: float
    cohorts: tuple[CohortEvidence, ...]


def evaluate_segmentations(sessions: tuple[EvaluatedSession, ...]) -> SegmentationEvidence:
    trades = tuple(trade for session in sessions for trade in session.trades)
    assignments = tuple(feature for session in sessions for feature in session.features)
    assigned = {row.recommendation_id: row for row in assignments if row.status is FeatureStatus.COMPLETE}
    complete_count = sum(trade.recommendation_id in assigned for trade in trades)
    gap_count = sum(
        assigned[trade.recommendation_id].gap_bucket is not None
        for trade in trades
        if trade.recommendation_id in assigned
    )
    trade_count = len(trades)
    return SegmentationEvidence(
        regime_coverage=(0.0 if not sessions else sum(row.regime is not None for row in sessions) / len(sessions)),
        regimes=_regime_evidence(sessions),
        feature_coverage=0.0 if not trades else complete_count / trade_count,
        gap_feature_coverage=0.0 if not trades else gap_count / trade_count,
        cohorts=_cohort_evidence(trades, assigned),
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


def _cohort_evidence(
    trades: tuple[PaperTrade, ...],
    assignments: dict[str, TradeFeatureAssignment],
) -> tuple[CohortEvidence, ...]:
    trade_by_id = {row.recommendation_id: row for row in trades}
    grouped: dict[tuple[CohortDimension, str], list[PaperTrade]] = {}
    for recommendation_id, assignment in assignments.items():
        trade = trade_by_id.get(recommendation_id)
        if trade is None:
            continue
        buckets = (
            (CohortDimension.PRICE, assignment.price_bucket),
            (CohortDimension.OPENING_GAP, assignment.gap_bucket),
            (CohortDimension.VOLUME_TO_ADV, assignment.volume_to_adv_bucket),
            (CohortDimension.DOLLAR_VOLUME, assignment.dollar_volume_bucket),
        )
        for dimension, bucket in buckets:
            if bucket is not None:
                grouped.setdefault((dimension, bucket.value), []).append(trade)
    evidence: list[CohortEvidence] = []
    for dimension, bucket in sorted(grouped, key=lambda item: (item[0].value, item[1])):
        metrics = summarize_performance(
            tuple(grouped[(dimension, bucket)]),
            MetricsConfig(20, BOOTSTRAP_SAMPLES, BOOTSTRAP_SEED),
        )
        evidence.append(
            CohortEvidence(
                dimension=dimension,
                bucket=bucket,
                trade_count=metrics.trade_count,
                average_return=metrics.average_return,
                profit_factor=metrics.profit_factor,
                mean_ci_low=metrics.mean_ci_low,
                mean_ci_high=metrics.mean_ci_high,
            )
        )
    return tuple(evidence)
