from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, assert_never, override
from zoneinfo import ZoneInfo

from trading_agent.kr_day_candidate_admission import assess_kr_day_candidate_admission
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluationRequest
from trading_agent.kr_day_decision_models import (
    KrDayCandidateAdmissionPolicy,
    KrDayCandidateAdmissionRequest,
    KrDayConditionalPlan,
    KrDayDecisionEvidenceValue,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_price_grid import round_kr_equity_price_up
from trading_agent.kr_theme_day_setup import assess_kr_theme_day_setup
from trading_agent.kr_theme_day_setup_progress import (
    KrThemeDayConditionalSetup,
    KrThemeDaySetupInput,
    KrThemeDaySetupPhase,
)
from trading_agent.kr_theme_day_signal import KrThemeDaySetup

_SEOUL: Final = ZoneInfo("Asia/Seoul")


class InvalidKrDayDecisionProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day decision projection input is invalid"


@dataclass(frozen=True, slots=True)
class KrDayDecisionProjection:
    status: KrDayDecisionStatus
    reason_codes: tuple[KrDayDecisionReasonCode, ...]
    plan: KrDayConditionalPlan | None
    evidence_refs: tuple[str, ...]
    observed_evidence: tuple[KrDayDecisionEvidenceValue, ...]
    valid_until: dt.datetime


def project_kr_day_decision(
    request: KrDayCapsuleEvaluationRequest,
    active_thesis_keys: tuple[str, ...],
    investigated_before: bool,
) -> KrDayDecisionProjection:
    policy = _policy(request)
    admission = assess_kr_day_candidate_admission(
        KrDayCandidateAdmissionRequest(
            policy=policy,
            capsule_id=request.capsule.capsule_id,
            hypothesis_version_id=request.capsule.hypothesis_version_id,
            opportunity=request.opportunity,
            market=request.market,
            bars=request.bars,
            evaluated_at=request.evaluated_at,
            active_thesis_keys=active_thesis_keys,
        )
    )
    if not admission.admitted:
        deadline = (
            _expired_deadline(request) if admission.status is KrDayDecisionStatus.EXPIRED else _future_deadline(request)
        )
        return KrDayDecisionProjection(
            admission.status,
            admission.reason_codes,
            None,
            admission.source_evidence_refs,
            admission.observed_evidence,
            deadline,
        )
    assessment = assess_kr_theme_day_setup(
        KrThemeDaySetupInput(
            opportunity=request.opportunity,
            bars=request.bars,
            producer_strategy_version=request.capsule.capsule_id,
            evaluated_at=request.evaluated_at,
            max_slippage_bps=request.max_slippage_bps,
        )
    )
    refs = tuple(
        sorted(set((*admission.source_evidence_refs, *(item.canonical_id for item in assessment.evidence_refs))))
    )
    deadline = _future_deadline(request)
    if deadline <= request.evaluated_at:
        return KrDayDecisionProjection(
            KrDayDecisionStatus.EXPIRED,
            (KrDayDecisionReasonCode.PRICE_SETUP_EXPIRED,),
            None,
            refs,
            admission.observed_evidence,
            max(request.bars[-1].end_at, min(deadline, request.evaluated_at)),
        )
    match assessment.phase:
        case KrThemeDaySetupPhase.NO_IMPULSE | KrThemeDaySetupPhase.IMPULSE_ONLY:
            status = KrDayDecisionStatus.REJECTED if investigated_before else KrDayDecisionStatus.INVESTIGATING
            return KrDayDecisionProjection(
                status,
                (KrDayDecisionReasonCode.PRICE_SETUP_INCOMPLETE,),
                None,
                refs,
                admission.observed_evidence,
                deadline,
            )
        case KrThemeDaySetupPhase.PULLBACK_FOUND:
            conditional = assessment.conditional
            if conditional is None:
                raise InvalidKrDayDecisionProjectionError
            plan = _conditional_plan(request, conditional, refs)
            return KrDayDecisionProjection(
                KrDayDecisionStatus.ARMED,
                (KrDayDecisionReasonCode.CONDITIONAL_TRIGGER_PENDING,),
                plan,
                refs,
                admission.observed_evidence,
                plan.valid_until,
            )
        case KrThemeDaySetupPhase.RECLAIM_CONFIRMED:
            plan = _reclaim_plan(request, assessment.setup, refs)
            trigger_evidence = _trigger_evidence(request)
            observed = (*admission.observed_evidence, *trigger_evidence)
            if plan is None:
                return KrDayDecisionProjection(
                    KrDayDecisionStatus.REJECTED,
                    (KrDayDecisionReasonCode.INVALID_ENTRY_LADDER,),
                    None,
                    refs,
                    observed,
                    deadline,
                )
            return KrDayDecisionProjection(
                KrDayDecisionStatus.ARMED,
                (KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,),
                plan,
                refs,
                observed,
                plan.valid_until,
            )
        case KrThemeDaySetupPhase.SETUP_EXPIRED:
            return KrDayDecisionProjection(
                KrDayDecisionStatus.EXPIRED,
                (KrDayDecisionReasonCode.PRICE_SETUP_EXPIRED,),
                None,
                refs,
                admission.observed_evidence,
                request.evaluated_at,
            )
        case unreachable:
            assert_never(unreachable)


def _policy(request: KrDayCapsuleEvaluationRequest) -> KrDayCandidateAdmissionPolicy:
    capsule = request.capsule
    if capsule.risk_policy_ref != "risk-policy://day-research/v1" or request.max_slippage_bps != Decimal("20"):
        raise InvalidKrDayDecisionProjectionError
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
    setup: KrThemeDaySetup | None,
    refs: tuple[str, ...],
) -> KrDayConditionalPlan | None:
    ask = request.market.ask_price
    if setup is None or ask is None:
        return None
    trigger = round_kr_equity_price_up(ask)
    if not setup.stop_price < trigger < setup.targets[0].price:
        return None
    return KrDayConditionalPlan(
        trigger_rule="Current fresh normalized ask after the latest completed-bar reclaim; no fill is asserted.",
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


def _trigger_evidence(request: KrDayCapsuleEvaluationRequest) -> tuple[KrDayDecisionEvidenceValue, ...]:
    ask = request.market.ask_price
    if ask is None:
        return (KrDayDecisionEvidenceValue(name="entry_trigger_ask", value="missing"),)
    normalized = round_kr_equity_price_up(ask)
    return (
        KrDayDecisionEvidenceValue(name="entry_trigger_ask", value=str(ask)),
        KrDayDecisionEvidenceValue(name="entry_trigger_normalized", value=str(normalized)),
    )


def _future_deadline(request: KrDayCapsuleEvaluationRequest) -> dt.datetime:
    return min(
        request.opportunity.valid_until,
        request.evaluated_at.astimezone(_SEOUL).replace(hour=15, minute=30, second=0, microsecond=0),
    )


def _expired_deadline(request: KrDayCapsuleEvaluationRequest) -> dt.datetime:
    return max(request.bars[-1].end_at, min(request.opportunity.valid_until, request.evaluated_at))


__all__ = (
    "InvalidKrDayDecisionProjectionError",
    "KrDayDecisionProjection",
    "project_kr_day_decision",
)
