from __future__ import annotations

from statistics import fmean

from trading_agent.day_agent_evaluation_metrics import AgentEvaluationMetrics
from trading_agent.day_agent_forward_shadow_controller import (
    DayForwardShadowSessionEvidence,
    DayForwardShadowSessionRequest,
)


def aggregate_capsule_metrics(
    evidence: tuple[DayForwardShadowSessionEvidence, ...],
    sessions: tuple[DayForwardShadowSessionRequest, ...],
    capsule_id: str,
) -> AgentEvaluationMetrics:
    metrics = tuple(_session_metrics(item, sessions[index], capsule_id) for index, item in enumerate(evidence))
    return AgentEvaluationMetrics(
        theme_timing=fmean(item.theme_timing for item in metrics),
        leader_rank=fmean(item.leader_rank for item in metrics),
        recommendation_calibration=fmean(item.recommendation_calibration for item in metrics),
        mfe=fmean(item.mfe for item in metrics),
        mae=fmean(item.mae for item in metrics),
        cost_adjusted_modeled_result=fmean(item.cost_adjusted_modeled_result for item in metrics),
        no_trade_quality=fmean(item.no_trade_quality for item in metrics),
        evidence_fidelity=fmean(item.evidence_fidelity for item in metrics),
        provenance_ids=tuple(sorted({item for metric in metrics for item in metric.provenance_ids})),
    )


def _session_metrics(
    evidence: DayForwardShadowSessionEvidence,
    source: DayForwardShadowSessionRequest,
    capsule_id: str,
) -> AgentEvaluationMetrics:
    results = tuple(item for tick in evidence.tick_results for item in tick.results if item.capsule_id == capsule_id)
    trial_ids = {item.trial_id for item in results}
    signal = next((item for item in evidence.signals if item.capsule_id == capsule_id), None)
    outcome = next((item for item in evidence.outcomes if item.trial_id in trial_ids), None)
    first_candidate = next((item.tick.candidate for item in source.ticks if item.tick.candidate is not None), None)
    reference_entry = (
        float(signal.signal.entry_price)
        if signal is not None
        else 0.0
        if first_candidate is None
        else first_candidate.price
    )
    observed_at = source.ticks[0].tick.observed_at if signal is None else signal.signal.observed_at
    bars = tuple(bar for item in source.ticks for bar in item.tick.bars if bar.timestamp >= observed_at)
    mfe = (
        0.0
        if reference_entry == 0.0
        else _clamp(max((bar.high / reference_entry - 1.0 for bar in bars), default=0.0), 0.0, 1.0)
    )
    mae = (
        0.0
        if reference_entry == 0.0
        else _clamp(min((bar.low / reference_entry - 1.0 for bar in bars), default=0.0), -1.0, 0.0)
    )
    cost_adjusted = 0.0 if outcome is None else _clamp(float(outcome.cost_adjusted_return), -1.0, 1.0)
    realized_confidence = _clamp((cost_adjusted + 0.1) / 0.2, 0.0, 1.0)
    if signal is None:
        theme_timing = 0.0
        leader_rank = 0.0 if first_candidate is None else _clamp(first_candidate.relative_volume / 5.0, 0.0, 1.0)
        implied_confidence = 0.0
    else:
        matching = next(item for item in source.ticks if item.tick.completed_bar_id == signal.completed_bar_id)
        candidate = matching.tick.candidate
        theme_timing = (len(source.ticks) - source.ticks.index(matching)) / len(source.ticks)
        leader_rank = 0.0 if candidate is None else _clamp(candidate.relative_volume / 5.0, 0.0, 1.0)
        risk = float(signal.signal.entry_price - signal.signal.stop_price)
        reward = float(signal.signal.targets[-1].price - signal.signal.entry_price)
        implied_confidence = _clamp(reward / (reward + risk), 0.0, 1.0)
    recommendation_calibration = 1.0 - abs(implied_confidence - realized_confidence)
    opportunity_quality = _clamp(mfe * 10.0, 0.0, 1.0)
    no_trade_quality = 1.0 - opportunity_quality if signal is None else opportunity_quality
    source_refs = {ref.canonical_id for item in source.ticks for ref in item.tick.evidence_refs}
    signal_refs = set() if signal is None else {ref.canonical_id for ref in signal.signal.evidence_refs}
    trusted_signal_refs = source_refs | {
        f"day/strategy_capsule:{capsule_id}",
        *(f"market/completed_bar:{item}" for item in evidence.completed_bar_ids),
        *(item for item in signal_refs if item.startswith("day/cost_model:")),
    }
    provenance_ids = tuple(
        sorted(
            {
                *evidence.completed_bar_ids,
                *(event_id for item in results for event_id in item.event_ids),
                *((signal.artifact_id,) if signal is not None else ()),
                *((outcome.outcome_id,) if outcome is not None else ()),
            }
        )
    )
    return AgentEvaluationMetrics(
        theme_timing=theme_timing,
        leader_rank=leader_rank,
        recommendation_calibration=recommendation_calibration,
        mfe=mfe,
        mae=mae,
        cost_adjusted_modeled_result=cost_adjusted,
        no_trade_quality=no_trade_quality,
        evidence_fidelity=float(signal_refs <= trusted_signal_refs),
        provenance_ids=provenance_ids,
    )


def _clamp(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


__all__ = ("aggregate_capsule_metrics",)
