from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Self, assert_never

from pydantic import Field, model_validator

from trading_agent.day_research_review_types import (
    DayExecutionAuthorityClass,
    DayExecutionEligibilityStatus,
    DayReviewModel,
    InvalidDayResearchReviewError,
    day_review_content_id,
)
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import aware

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REASON = re.compile(r"^[a-z0-9][a-z0-9_]{0,127}$")


class DayOwnerAuthorityEventPayload(DayReviewModel):
    decision_id: str = Field(pattern=_SHA256_PATTERN)
    capsule_id: str = Field(pattern=_SHA256_PATTERN)
    hypothesis_version_id: str = Field(pattern=_SHA256_PATTERN)
    market_id: MarketId
    authority_class: DayExecutionAuthorityClass
    owner_id: str
    authority_role: Literal["owner"] = "owner"
    approved_at: dt.datetime
    effective_after_session: dt.date

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        if (
            self.market_id is not MarketId.US_EQUITIES
            or _IDENTIFIER.fullmatch(self.owner_id) is None
            or not aware(self.approved_at)
        ):
            raise InvalidDayResearchReviewError("day_owner_authority_event_invalid")
        return self


class DayOwnerAuthorityEvent(DayReviewModel):
    authority_event_id: str = Field(pattern=_SHA256_PATTERN)
    payload: DayOwnerAuthorityEventPayload

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.authority_event_id != day_review_content_id(self.payload):
            raise InvalidDayResearchReviewError("day_owner_authority_identity_invalid")
        return self


class ExecutionEligibilityPayload(DayReviewModel):
    decision_id: str = Field(pattern=_SHA256_PATTERN)
    capsule_id: str = Field(pattern=_SHA256_PATTERN)
    hypothesis_version_id: str = Field(pattern=_SHA256_PATTERN)
    market_id: MarketId
    session_date: dt.date
    sequence: int = Field(ge=1)
    previous_event_id: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    clean_commit_sha256: str = Field(pattern=_SHA256_PATTERN)
    risk_policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    authority_event: DayOwnerAuthorityEvent | None
    effective_at: dt.datetime
    expires_at: dt.datetime
    status: DayExecutionEligibilityStatus
    broker_blocked: bool
    blockers: tuple[str, ...]
    paper_order_authority: bool

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if (
            not aware(self.effective_at)
            or not aware(self.expires_at)
            or (self.sequence == 1) is not (self.previous_event_id is None)
            or self.blockers != tuple(sorted(set(self.blockers)))
            or any(_REASON.fullmatch(reason) is None for reason in self.blockers)
        ):
            raise InvalidDayResearchReviewError("day_execution_eligibility_payload_invalid")
        authority = self.authority_event
        if authority is not None and (
            authority.payload.decision_id != self.decision_id
            or authority.payload.capsule_id != self.capsule_id
            or authority.payload.hypothesis_version_id != self.hypothesis_version_id
            or authority.payload.market_id is not self.market_id
        ):
            raise InvalidDayResearchReviewError("day_execution_owner_authority_mismatch")
        return self


class ExecutionEligibility(DayReviewModel):
    eligibility_event_id: str = Field(pattern=_SHA256_PATTERN)
    payload: ExecutionEligibilityPayload

    @model_validator(mode="after")
    def validate_eligibility(self) -> Self:
        if self.eligibility_event_id != day_review_content_id(self.payload):
            raise InvalidDayResearchReviewError("day_execution_eligibility_identity_invalid")
        match self.payload.market_id:
            case MarketId.US_EQUITIES:
                self._validate_us()
            case MarketId.KR_EQUITIES:
                self._validate_kr()
            case unreachable:
                assert_never(unreachable)
        return self

    def _validate_us(self) -> None:
        payload = self.payload
        if payload.broker_blocked:
            raise InvalidDayResearchReviewError("day_execution_us_broker_block_invalid")
        match payload.status:
            case DayExecutionEligibilityStatus.ELIGIBLE:
                authority = payload.authority_event
                if (
                    authority is None
                    or authority.payload.approved_at > payload.effective_at
                    or payload.session_date < authority.payload.effective_after_session
                    or payload.blockers
                    or not payload.paper_order_authority
                    or payload.expires_at <= payload.effective_at
                ):
                    raise InvalidDayResearchReviewError("day_execution_owner_authority_required")
            case DayExecutionEligibilityStatus.BLOCKED:
                if not payload.blockers or payload.paper_order_authority or payload.expires_at <= payload.effective_at:
                    raise InvalidDayResearchReviewError("day_execution_blocked_status_invalid")
            case DayExecutionEligibilityStatus.SUSPENDED:
                if payload.authority_event is None or not payload.blockers or payload.paper_order_authority:
                    raise InvalidDayResearchReviewError("day_execution_suspended_status_invalid")
            case DayExecutionEligibilityStatus.EXPIRED:
                if payload.authority_event is None or not payload.blockers or payload.paper_order_authority:
                    raise InvalidDayResearchReviewError("day_execution_expired_status_invalid")
                if payload.expires_at > payload.effective_at:
                    raise InvalidDayResearchReviewError("day_execution_expiry_invalid")
            case unreachable:
                assert_never(unreachable)

    def _validate_kr(self) -> None:
        payload = self.payload
        if (
            payload.status is not DayExecutionEligibilityStatus.BLOCKED
            or not payload.broker_blocked
            or payload.authority_event is not None
            or payload.blockers != ("provider_read_only",)
            or payload.paper_order_authority
            or payload.expires_at <= payload.effective_at
        ):
            raise InvalidDayResearchReviewError("day_execution_kr_broker_block_required")


__all__ = (
    "DayOwnerAuthorityEvent",
    "DayOwnerAuthorityEventPayload",
    "ExecutionEligibility",
    "ExecutionEligibilityPayload",
)
