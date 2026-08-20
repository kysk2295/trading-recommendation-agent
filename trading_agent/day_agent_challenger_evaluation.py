from __future__ import annotations

import hashlib
from statistics import fmean

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from trading_agent.day_agent_forward_shadow_controller import (
    DayForwardShadowRunner,
    DayForwardShadowSessionEvidence,
    DayForwardShadowSessionRequest,
    DayForwardShadowTickRequest,
    UsForwardShadowControllerRunner,
)
from trading_agent.day_agent_version_models import (
    AgentDeploymentTransition,
    AgentPromotionDecision,
    AgentPromotionRecommendation,
    AgentVersion,
    DayAgentVersionStoreError,
)
from trading_agent.day_agent_version_store import DayAgentVersionStore
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json


class DayAgentChallengerEvaluationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    champion: AgentVersion
    challenger: AgentVersion
    champion_capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    challenger_capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    sessions: tuple[DayForwardShadowSessionRequest, ...] = Field(min_length=2, max_length=20)
    minimum_sessions: int = Field(ge=2, le=20)
    evaluated_at: AwareDatetime


def evaluate_day_agent_challenger(
    request: DayAgentChallengerEvaluationRequest,
    store: DayAgentVersionStore,
    runner: DayForwardShadowRunner,
) -> AgentPromotionRecommendation:
    checked = DayAgentChallengerEvaluationRequest.model_validate(request.model_dump(mode="python"))
    stored_champion = store.reader().champion()
    stored_challenger = store.reader().challenger(checked.challenger.version_id)
    capsule_ids = (checked.champion_capsule_id, checked.challenger_capsule_id)
    if (
        stored_champion != checked.champion
        or stored_challenger != checked.challenger
        or checked.challenger.parent_version_id != checked.champion.version_id
        or checked.champion_capsule_id not in checked.champion.playbook_ids
        or checked.challenger_capsule_id not in checked.challenger.playbook_ids
        or checked.champion_capsule_id == checked.challenger_capsule_id
    ):
        raise DayAgentVersionStoreError("future_shadow_version_invalid")
    evidence = tuple(runner.run_session(item, capsule_ids) for item in checked.sessions)
    _validate_controller_evidence(checked, evidence, capsule_ids)
    champion_score = fmean(
        _capsule_session_score(item, checked.sessions[index], capsule_ids[0]) for index, item in enumerate(evidence)
    )
    challenger_score = fmean(
        _capsule_session_score(item, checked.sessions[index], capsule_ids[1]) for index, item in enumerate(evidence)
    )
    decision, reasons = _promotion_decision(champion_score, challenger_score)
    sessions = tuple(item.session_date for item in evidence)
    paired_ids = tuple(hashlib.sha256(":".join(item.completed_bar_ids).encode()).hexdigest() for item in evidence)
    controller_ids = tuple(
        sorted(
            {
                *(
                    event_id
                    for item in evidence
                    for tick_result in item.tick_results
                    for result in tick_result.results
                    for event_id in result.event_ids
                ),
                *(item.artifact_id for session in evidence for item in session.signals),
                *(item.outcome_id for session in evidence for item in session.outcomes),
            }
        )
    )
    unsigned = AgentPromotionRecommendation(
        recommendation_id="0" * 64,
        champion_version_id=checked.champion.version_id,
        challenger_version_id=checked.challenger.version_id,
        decision=decision,
        evaluated_session_dates=sessions,
        paired_snapshot_ids=paired_ids,
        controller_evidence_ids=controller_ids,
        champion_score=champion_score,
        challenger_score=challenger_score,
        reason_codes=reasons,
        evaluated_at=checked.evaluated_at,
    )
    identity = hashlib.sha256(canonical_experiment_ledger_json(unsigned).encode()).hexdigest()
    recommendation = unsigned.model_copy(update={"recommendation_id": identity})
    with store.writer() as writer:
        _ = writer._record_controller_recommendation(recommendation)
    return recommendation


def _validate_controller_evidence(
    request: DayAgentChallengerEvaluationRequest,
    evidence: tuple[DayForwardShadowSessionEvidence, ...],
    capsule_ids: tuple[str, str],
) -> None:
    session_dates = tuple(item.session_date for item in evidence)
    if (
        len(evidence) < request.minimum_sessions
        or session_dates != tuple(sorted(set(session_dates)))
        or any(item <= request.challenger.created_session_date for item in session_dates)
    ):
        raise DayAgentVersionStoreError("future_shadow_pairing_invalid")
    for source, result in zip(request.sessions, evidence, strict=True):
        expected_ids = tuple(item.tick.completed_bar_id for item in source.ticks)
        observed_capsules = {item.capsule_id for tick_result in result.tick_results for item in tick_result.results}
        artifact_trials = {item.trial_id for item in (*result.signals, *result.outcomes)}
        result_trials = {item.trial_id for tick_result in result.tick_results for item in tick_result.results}
        result_outcomes = {
            item.outcome_id
            for tick_result in result.tick_results
            for item in tick_result.results
            if item.outcome_id is not None
        }
        stored_outcomes = {item.outcome_id for item in result.outcomes}
        signals_by_trial = {item.trial_id: item for item in result.signals}
        if (
            result.session_id != source.ticks[0].tick.session_id
            or result.session_date != source.ticks[0].tick.session_date
            or result.completed_bar_ids != expected_ids
            or len(result.tick_results) != len(source.ticks)
            or not set(capsule_ids) <= observed_capsules
            or not artifact_trials <= result_trials
            or result_outcomes != stored_outcomes
            or any(item.completed_bar_id not in expected_ids for item in result.signals)
            or any(item.exit_completed_bar_id not in expected_ids for item in result.outcomes)
            or any(
                item.trial_id not in signals_by_trial
                or item.signal_artifact_id != signals_by_trial[item.trial_id].artifact_id
                for item in result.outcomes
            )
            or any(
                tick_result.completed_bar_id != tick_request.tick.completed_bar_id
                for tick_result, tick_request in zip(result.tick_results, source.ticks, strict=True)
            )
        ):
            raise DayAgentVersionStoreError("future_shadow_controller_evidence_invalid")


def _capsule_session_score(
    evidence: DayForwardShadowSessionEvidence,
    source: DayForwardShadowSessionRequest,
    capsule_id: str,
) -> float:
    results = tuple(item for tick in evidence.tick_results for item in tick.results if item.capsule_id == capsule_id)
    trial_ids = {item.trial_id for item in results}
    signal = next((item for item in evidence.signals if item.capsule_id == capsule_id), None)
    outcome = next((item for item in evidence.outcomes if item.trial_id in trial_ids), None)
    if signal is None:
        return 0.0
    entry = float(signal.signal.entry_price)
    bars = tuple(bar for item in source.ticks for bar in item.tick.bars if bar.timestamp >= signal.signal.observed_at)
    mfe = max((bar.high / entry - 1.0 for bar in bars), default=0.0)
    mae = min((bar.low / entry - 1.0 for bar in bars), default=0.0)
    candidate_symbols = {item.tick.candidate.symbol for item in source.ticks if item.tick.candidate is not None}
    evidence_refs = {ref.canonical_id for item in source.ticks for ref in item.tick.evidence_refs}
    signal_refs = {ref.canonical_id for ref in signal.signal.evidence_refs}
    cost_adjusted = 0.0 if outcome is None else float(outcome.cost_adjusted_return)
    return fmean(
        (
            1.0,
            float(signal.signal.symbol in candidate_symbols),
            mfe,
            mae,
            cost_adjusted,
            float(signal_refs <= evidence_refs),
        )
    )


def _promotion_decision(
    champion_score: float,
    challenger_score: float,
) -> tuple[AgentPromotionDecision, tuple[str, ...]]:
    margin = challenger_score - champion_score
    if margin >= 0.05:
        return AgentPromotionDecision.PROMOTE, ("challenger_margin_met",)
    if margin <= -0.05:
        return AgentPromotionDecision.ROLLBACK, ("challenger_regressed",)
    return AgentPromotionDecision.REJECT, ("challenger_margin_not_met",)


def deploy_recommended_challenger(
    recommendation: AgentPromotionRecommendation,
    store: DayAgentVersionStore,
) -> AgentDeploymentTransition:
    stored = store.reader().recommendations(recommendation.challenger_version_id)
    challenger = store.reader().challenger(recommendation.challenger_version_id)
    if (
        recommendation.decision is not AgentPromotionDecision.PROMOTE
        or recommendation not in stored
        or challenger is None
        or len(recommendation.evaluated_session_dates) < 2
        or any(item <= challenger.created_session_date for item in recommendation.evaluated_session_dates)
    ):
        raise DayAgentVersionStoreError("deployment_recommendation_invalid")
    unsigned = AgentDeploymentTransition(
        transition_id="0" * 64,
        recommendation_id=recommendation.recommendation_id,
        demoted_version_id=recommendation.champion_version_id,
        promoted_version_id=recommendation.challenger_version_id,
        deployed_at=recommendation.evaluated_at,
    )
    identity = hashlib.sha256(canonical_experiment_ledger_json(unsigned).encode()).hexdigest()
    transition = unsigned.model_copy(update={"transition_id": identity})
    with store.writer() as writer:
        _ = writer._apply_promotion(recommendation, transition)
    return transition


__all__ = (
    "DayAgentChallengerEvaluationRequest",
    "DayForwardShadowRunner",
    "DayForwardShadowSessionEvidence",
    "DayForwardShadowSessionRequest",
    "DayForwardShadowTickRequest",
    "UsForwardShadowControllerRunner",
    "deploy_recommended_challenger",
    "evaluate_day_agent_challenger",
)
