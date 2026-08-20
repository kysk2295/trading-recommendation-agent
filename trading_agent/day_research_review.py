from __future__ import annotations

import datetime as dt
from typing import Self, assert_never

from pydantic import Field, model_validator

from trading_agent.day_research_review_ledger import (
    append_execution_eligibility,
    record_promotion_decision,
)
from trading_agent.day_research_review_models import (
    DayOwnerAuthorityEvent,
    DayOwnerAuthorityEventPayload,
    DayReviewModel,
    ExecutionEligibility,
    ExecutionEligibilityPayload,
    PromotionDecision,
    PromotionDecisionPayload,
    ReviewFeedbackSummary,
)
from trading_agent.day_research_review_types import (
    DayExecutionEligibilityStatus,
    InvalidDayResearchReviewError,
    day_review_content_id,
    required_day_execution_authority_class,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import aware

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class DayExecutionSessionContext(DayReviewModel):
    session_date: dt.date
    sequence: int = Field(ge=1)
    previous_event_id: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    clean_commit_sha256: str = Field(pattern=_SHA256_PATTERN)
    risk_policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    effective_at: dt.datetime
    expires_at: dt.datetime

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        if (
            not aware(self.effective_at)
            or not aware(self.expires_at)
            or self.expires_at <= self.effective_at
            or (self.sequence == 1) is not (self.previous_event_id is None)
        ):
            raise InvalidDayResearchReviewError("day_execution_session_context_invalid")
        return self


def seal_promotion_decision(payload: PromotionDecisionPayload) -> PromotionDecision:
    return PromotionDecision(decision_id=day_review_content_id(payload), payload=payload)


def seal_owner_authority_event(payload: DayOwnerAuthorityEventPayload) -> DayOwnerAuthorityEvent:
    return DayOwnerAuthorityEvent(
        authority_event_id=day_review_content_id(payload),
        payload=payload,
    )


def build_execution_eligibility(
    decision: PromotionDecision,
    context: DayExecutionSessionContext,
    authority_event: DayOwnerAuthorityEvent | None = None,
) -> ExecutionEligibility:
    if context.session_date < decision.payload.effective_after_session:
        raise InvalidDayResearchReviewError("day_execution_session_precedes_promotion")
    status, broker_blocked, blockers, selected_authority, order_authority = _eligibility_state(
        decision,
        authority_event,
    )
    payload = ExecutionEligibilityPayload(
        decision_id=decision.decision_id,
        capsule_id=decision.payload.capsule_id,
        hypothesis_version_id=decision.payload.hypothesis_version_id,
        market_id=decision.payload.market_id,
        session_date=context.session_date,
        sequence=context.sequence,
        previous_event_id=context.previous_event_id,
        clean_commit_sha256=context.clean_commit_sha256,
        risk_policy_sha256=context.risk_policy_sha256,
        authority_event=selected_authority,
        effective_at=context.effective_at,
        expires_at=context.expires_at,
        status=status,
        broker_blocked=broker_blocked,
        blockers=blockers,
        paper_order_authority=order_authority,
    )
    return ExecutionEligibility(
        eligibility_event_id=day_review_content_id(payload),
        payload=payload,
    )


def _eligibility_state(
    decision: PromotionDecision,
    authority_event: DayOwnerAuthorityEvent | None,
) -> tuple[
    DayExecutionEligibilityStatus,
    bool,
    tuple[str, ...],
    DayOwnerAuthorityEvent | None,
    bool,
]:
    match decision.payload.market_id:
        case MarketId.KR_EQUITIES:
            return DayExecutionEligibilityStatus.BLOCKED, True, ("provider_read_only",), None, False
        case MarketId.US_EQUITIES:
            expected = required_day_execution_authority_class(decision.payload.status)
            if expected is None:
                return (
                    DayExecutionEligibilityStatus.BLOCKED,
                    False,
                    ("promotion_not_paper_candidate",),
                    None,
                    False,
                )
            if authority_event is None or authority_event.payload.authority_class is not expected:
                return (
                    DayExecutionEligibilityStatus.BLOCKED,
                    False,
                    ("owner_approval_required",),
                    None,
                    False,
                )
            return DayExecutionEligibilityStatus.ELIGIBLE, False, (), authority_event, True
        case unreachable:
            assert_never(unreachable)


def build_review_feedback(
    decision: PromotionDecision,
    next_review_date: dt.date,
    reason_codes: tuple[str, ...],
) -> ReviewFeedbackSummary:
    evidence = decision.payload.historical_evidence_seal.payload
    return ReviewFeedbackSummary(
        decision_id=decision.decision_id,
        capsule_id=decision.payload.capsule_id,
        market_id=decision.payload.market_id,
        status=decision.payload.status,
        classification=evidence.classification,
        reason_codes=reason_codes,
        selection_diagnostics_status=evidence.selection_diagnostics.status,
        power_ci_sufficient=(decision.payload.selection_adjusted_statistics.power_ci_sufficient),
        next_review_date=next_review_date,
    )


__all__ = (
    "DayExecutionSessionContext",
    "append_execution_eligibility",
    "build_execution_eligibility",
    "build_review_feedback",
    "record_promotion_decision",
    "seal_owner_authority_event",
    "seal_promotion_decision",
)
