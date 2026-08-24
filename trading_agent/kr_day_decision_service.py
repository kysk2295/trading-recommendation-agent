from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final, assert_never, override
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.kr_day_candidate_admission import assess_kr_day_candidate_admission
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluationRequest
from trading_agent.kr_day_decision_models import (
    KrDayCandidateAdmissionPolicy,
    KrDayCandidateAdmissionRequest,
    KrDayConditionalPlan,
    KrDayDecisionEvent,
    KrDayDecisionEventPayload,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.kr_theme_day_setup import assess_kr_theme_day_setup
from trading_agent.kr_theme_day_setup_progress import (
    KrThemeDayConditionalSetup,
    KrThemeDaySetupAssessment,
    KrThemeDaySetupInput,
    KrThemeDaySetupPhase,
)

_SEOUL: Final = ZoneInfo("Asia/Seoul")


class InvalidKrDayDecisionServiceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day decision service input is invalid"


def run_kr_day_decision_tick(
    requests: tuple[KrDayCapsuleEvaluationRequest, ...],
    store: KrDayDecisionStore,
) -> tuple[KrDayDecisionEvent, ...]:
    if len(requests) > 3:
        raise InvalidKrDayDecisionServiceError
    accepted: list[KrDayCapsuleEvaluationRequest] = []
    for item in requests:
        try:
            accepted.append(KrDayCapsuleEvaluationRequest.model_validate(item.model_dump(mode="python")))
        except (AttributeError, TypeError, ValidationError, ValueError):
            continue
    current = tuple(sorted(accepted, key=lambda item: item.capsule.capsule_id))
    if len({item.capsule.capsule_id for item in current}) != len(current):
        raise InvalidKrDayDecisionServiceError
    if len({(item.opportunity.opportunity_id, item.bars[-1].end_at, item.evaluated_at) for item in current}) > 1:
        raise InvalidKrDayDecisionServiceError
    persisted = store.events()
    return tuple(_decide(item, store, persisted) for item in current)


def _decide(
    request: KrDayCapsuleEvaluationRequest,
    store: KrDayDecisionStore,
    persisted: tuple[KrDayDecisionEvent, ...],
) -> KrDayDecisionEvent:
    capsule = request.capsule
    opportunity = request.opportunity
    completed_bar_at = request.bars[-1].end_at
    session_date = completed_bar_at.astimezone(_SEOUL).date()
    same_bar = tuple(
        item
        for item in persisted
        if item.session_date == session_date
        and item.capsule_id == capsule.capsule_id
        and item.opportunity_id == opportunity.opportunity_id
        and item.completed_bar_at == completed_bar_at
    )
    if len(same_bar) > 1:
        raise InvalidKrDayDecisionServiceError
    if same_bar:
        return same_bar[0]
    previous = store.latest(capsule.capsule_id, opportunity.opportunity_id, session_date)
    policy = _policy(request)
    active_theses = tuple(
        sorted(
            {
                evidence.value
                for event in persisted
                if event.session_date == session_date
                and event.status is KrDayDecisionStatus.ARMED
                and (event.capsule_id, event.opportunity_id) != (capsule.capsule_id, opportunity.opportunity_id)
                for evidence in event.observed_evidence
                if evidence.name == "thesis_key"
            }
        )
    )
    admission = assess_kr_day_candidate_admission(
        KrDayCandidateAdmissionRequest(
            policy=policy,
            capsule_id=capsule.capsule_id,
            hypothesis_version_id=capsule.hypothesis_version_id,
            opportunity=opportunity,
            market=request.market,
            bars=request.bars,
            evaluated_at=request.evaluated_at,
            active_thesis_keys=active_theses,
        )
    )
    status = admission.status
    reasons = admission.reason_codes
    plan: KrDayConditionalPlan | None = None
    evidence_refs = admission.source_evidence_refs
    valid_until = _future_deadline(request)
    if admission.admitted:
        assessment = assess_kr_theme_day_setup(
            KrThemeDaySetupInput(
                opportunity=opportunity,
                bars=request.bars,
                producer_strategy_version=capsule.capsule_id,
                evaluated_at=request.evaluated_at,
                max_slippage_bps=request.max_slippage_bps,
            )
        )
        status, reasons, plan, evidence_refs, valid_until = _setup_disposition(
            assessment,
            request,
            previous,
            evidence_refs,
        )
    elif status is KrDayDecisionStatus.EXPIRED:
        valid_until = opportunity.valid_until
    payload = KrDayDecisionEventPayload(
        capsule_id=capsule.capsule_id,
        hypothesis_version_id=capsule.hypothesis_version_id,
        opportunity_id=opportunity.opportunity_id,
        session_date=session_date,
        symbol=opportunity.candidates[0].symbol,
        completed_bar_at=completed_bar_at,
        observed_at=request.evaluated_at,
        valid_until=valid_until,
        status=status,
        reason_codes=reasons,
        conditional_plan=plan,
        evidence_refs=evidence_refs,
        observed_evidence=admission.observed_evidence,
        previous_event_id=None if previous is None else previous.event_id,
    )
    event = KrDayDecisionEvent.model_validate(
        payload.model_dump(mode="python") | {"event_id": KrDayDecisionEvent.canonical_id_for(payload)}
    )
    _ = store.append(event)
    return event


def _policy(request: KrDayCapsuleEvaluationRequest) -> KrDayCandidateAdmissionPolicy:
    capsule = request.capsule
    if capsule.risk_policy_ref != "risk-policy://day-research/v1" or request.max_slippage_bps != Decimal("20"):
        raise InvalidKrDayDecisionServiceError
    draft = KrDayCandidateAdmissionPolicy.model_construct(
        policy_version="kr-day-candidate-admission-v1",
        policy_id="0" * 64,
        capsule_id=capsule.capsule_id,
        hypothesis_version_id=capsule.hypothesis_version_id,
        min_related_symbol_count=2,
        min_catalyst_count=1,
        min_publisher_count=1,
        min_opportunity_volume_ratio=Decimal("1.2"),
        min_completed_bar_volume_ratio=Decimal("1.2"),
        min_trading_value_krw=Decimal("1000000"),
        min_completed_bar_trading_value_krw=Decimal("10000"),
        min_completed_bar_price_response=Decimal("0.000001"),
        max_spread_bps=Decimal("20"),
    )
    return KrDayCandidateAdmissionPolicy.model_validate(
        draft.model_dump(mode="python") | {"policy_id": KrDayCandidateAdmissionPolicy.canonical_id_for(draft)}
    )


def _setup_disposition(
    assessment: KrThemeDaySetupAssessment,
    request: KrDayCapsuleEvaluationRequest,
    previous: KrDayDecisionEvent | None,
    admission_refs: tuple[str, ...],
) -> tuple[
    KrDayDecisionStatus,
    tuple[KrDayDecisionReasonCode, ...],
    KrDayConditionalPlan | None,
    tuple[str, ...],
    dt.datetime,
]:
    refs = tuple(sorted(set((*admission_refs, *(item.canonical_id for item in assessment.evidence_refs)))))
    incomplete = (KrDayDecisionReasonCode.PRICE_SETUP_INCOMPLETE,)
    expired = (KrDayDecisionReasonCode.OPPORTUNITY_EXPIRED,)
    deadline = _future_deadline(request)
    match assessment.phase:
        case KrThemeDaySetupPhase.NO_IMPULSE | KrThemeDaySetupPhase.IMPULSE_ONLY:
            repeated = previous is not None and previous.status is KrDayDecisionStatus.INVESTIGATING
            status = KrDayDecisionStatus.REJECTED if repeated else KrDayDecisionStatus.INVESTIGATING
            return status, incomplete, None, refs, deadline
        case KrThemeDaySetupPhase.PULLBACK_FOUND:
            conditional = assessment.conditional
            if conditional is None:
                raise InvalidKrDayDecisionServiceError
            plan = _conditional_plan(request, conditional, refs)
            return KrDayDecisionStatus.ARMED, incomplete, plan, refs, plan.valid_until
        case KrThemeDaySetupPhase.RECLAIM_CONFIRMED:
            plan = _reclaim_plan(request, assessment, refs)
            if plan is None:
                return KrDayDecisionStatus.REJECTED, incomplete, None, refs, deadline
            return KrDayDecisionStatus.ARMED, incomplete, plan, refs, plan.valid_until
        case KrThemeDaySetupPhase.SETUP_EXPIRED:
            return KrDayDecisionStatus.EXPIRED, expired, None, refs, request.evaluated_at
        case unreachable:
            assert_never(unreachable)


def _conditional_plan(
    request: KrDayCapsuleEvaluationRequest,
    conditional: KrThemeDayConditionalSetup,
    refs: tuple[str, ...],
) -> KrDayConditionalPlan:
    return KrDayConditionalPlan(
        trigger_rule=conditional.trigger_rule,
        trigger_price=conditional.trigger_price,
        stop_price=conditional.stop_price,
        target_prices=conditional.target_prices,
        invalidation_rule=conditional.invalidation_rule,
        valid_until=min(conditional.valid_until, _future_deadline(request)),
        rationale=conditional.rationale,
        evidence_refs=refs,
        capsule_id=request.capsule.capsule_id,
        hypothesis_version_id=request.capsule.hypothesis_version_id,
    )


def _reclaim_plan(
    request: KrDayCapsuleEvaluationRequest,
    assessment: KrThemeDaySetupAssessment,
    refs: tuple[str, ...],
) -> KrDayConditionalPlan | None:
    setup = assessment.setup
    trigger = request.market.ask_price
    if setup is None or trigger is None or not setup.stop_price < trigger < setup.targets[0].price:
        return None
    return KrDayConditionalPlan(
        trigger_rule="Current fresh ask after the latest completed-bar reclaim; no fill is asserted.",
        trigger_price=trigger,
        stop_price=setup.stop_price,
        target_prices=tuple(item.price for item in setup.targets),
        invalidation_rule=setup.invalidation_rule,
        valid_until=min(setup.valid_until, _future_deadline(request)),
        rationale=setup.rationale,
        evidence_refs=refs,
        capsule_id=request.capsule.capsule_id,
        hypothesis_version_id=request.capsule.hypothesis_version_id,
    )


def _future_deadline(request: KrDayCapsuleEvaluationRequest) -> dt.datetime:
    return min(
        request.opportunity.valid_until,
        request.evaluated_at.astimezone(_SEOUL).replace(hour=15, minute=30, second=0, microsecond=0),
    )


__all__ = ("InvalidKrDayDecisionServiceError", "run_kr_day_decision_tick")
