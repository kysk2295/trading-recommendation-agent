from __future__ import annotations

import hashlib

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_agent_evaluation_metrics import AgentScoreComparison
from trading_agent.day_agent_forward_shadow_controller import (
    DayForwardShadowSessionEvidence,
    DayForwardShadowSessionRequest,
    DayForwardShadowTickRequest,
    UsForwardShadowControllerRunner,
)
from trading_agent.day_agent_shadow_scoring import aggregate_capsule_metrics
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
    safety_incident_ids: tuple[str, ...] = ()
    risk_incident_ids: tuple[str, ...] = ()
    data_incident_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_incidents(self) -> DayAgentChallengerEvaluationRequest:
        incidents = (
            self.safety_incident_ids,
            self.risk_incident_ids,
            self.data_incident_ids,
        )
        if any(items != tuple(sorted(set(items))) for items in incidents):
            raise DayAgentVersionStoreError("future_shadow_incidents_invalid")
        return self


def evaluate_day_agent_challenger(
    request: DayAgentChallengerEvaluationRequest,
    store: DayAgentVersionStore,
    controller: UsForwardShadowControllerRunner,
) -> AgentPromotionRecommendation:
    checked = DayAgentChallengerEvaluationRequest.model_validate(request.model_dump(mode="python"))
    stored_champion = store.reader().champion()
    stored_challenger = store.reader().challenger(checked.challenger.version_id)
    capsule_ids = (checked.champion_capsule_id, checked.challenger_capsule_id)
    if (
        type(controller) is not UsForwardShadowControllerRunner
        or stored_champion != checked.champion
        or stored_challenger != checked.challenger
        or checked.challenger.parent_version_id != checked.champion.version_id
        or checked.champion_capsule_id not in checked.champion.playbook_ids
        or checked.challenger_capsule_id not in checked.challenger.playbook_ids
        or checked.champion_capsule_id == checked.challenger_capsule_id
    ):
        raise DayAgentVersionStoreError("future_shadow_version_invalid")
    evidence = tuple(controller.run_session(item, capsule_ids) for item in checked.sessions)
    _validate_controller_evidence(checked, evidence, capsule_ids)
    comparison = AgentScoreComparison(
        champion=aggregate_capsule_metrics(evidence, checked.sessions, capsule_ids[0]),
        challenger=aggregate_capsule_metrics(evidence, checked.sessions, capsule_ids[1]),
    )
    gate_reasons = _promotion_gate_reasons(checked, evidence, controller)
    decision, reasons = (
        (AgentPromotionDecision.REJECT, gate_reasons)
        if gate_reasons
        else _promotion_decision(comparison.margin)
    )
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
                *(bar_id for session in evidence for bar_id in session.completed_bar_ids),
                *checked.safety_incident_ids,
                *checked.risk_incident_ids,
                *checked.data_incident_ids,
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
        comparison=comparison,
        reason_codes=reasons,
        evaluated_at=checked.evaluated_at,
    )
    identity = hashlib.sha256(canonical_experiment_ledger_json(unsigned).encode()).hexdigest()
    recommendation = unsigned.model_copy(update={"recommendation_id": identity})
    with store.writer() as writer:
        _ = writer._record_controller_recommendation(recommendation)
    return recommendation


def _promotion_gate_reasons(
    request: DayAgentChallengerEvaluationRequest,
    evidence: tuple[DayForwardShadowSessionEvidence, ...],
    controller: UsForwardShadowControllerRunner,
) -> tuple[str, ...]:
    reader = controller.services.ledger.reader()
    champion = reader.day_strategy_capsule(request.champion_capsule_id)
    challenger = reader.day_strategy_capsule(request.challenger_capsule_id)
    if champion is None or challenger is None:
        raise DayAgentVersionStoreError("future_shadow_capsule_lineage_invalid")
    capsule = challenger.capsule
    attempts = reader.day_attempts_for_review(capsule.market_id, capsule.hypothesis_version_id)
    attempt = next(
        (item for item in attempts if item.binding.binding_id == capsule.attempt_binding_id),
        None,
    )
    manifests = (
        ()
        if attempt is None
        else tuple(
            item
            for item in reader.strategy_research_preregistrations()
            if item.hypothesis.hypothesis_id == attempt.attempt.hypothesis_id
        )
    )
    if attempt is None or len(manifests) != 1:
        raise DayAgentVersionStoreError("future_shadow_capsule_lineage_invalid")
    hypothesis = manifests[0].hypothesis
    binding = attempt.binding
    observations = sum(len(session.completed_bar_ids) for session in evidence)
    reasons: list[str] = []
    if observations < hypothesis.minimum_observations:
        reasons.append("minimum_observations_not_met")
    if (
        capsule.resource_limits != champion.capsule.resource_limits
        or capsule.risk_policy_ref != champion.capsule.risk_policy_ref
    ):
        reasons.append("risk_limits_changed")
    if (
        binding.multiple_testing_family != hypothesis.multiple_testing_family
        or binding.search_budget_debit
        > min(binding.multiple_testing_budget, hypothesis.max_attempts)
    ):
        reasons.append("multiple_testing_budget_invalid")
    if request.safety_incident_ids or request.risk_incident_ids or request.data_incident_ids:
        reasons.append("safety_risk_or_data_incident")
    return tuple(sorted(reasons))


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


def _promotion_decision(margin: float) -> tuple[AgentPromotionDecision, tuple[str, ...]]:
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
    "DayForwardShadowSessionEvidence",
    "DayForwardShadowSessionRequest",
    "DayForwardShadowTickRequest",
    "UsForwardShadowControllerRunner",
    "deploy_recommended_challenger",
    "evaluate_day_agent_challenger",
)
