from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Self, assert_never

from pydantic import Field, model_validator

from trading_agent.day_execution_eligibility_models import (
    DayOwnerAuthorityEvent,
    DayOwnerAuthorityEventPayload,
    ExecutionEligibility,
    ExecutionEligibilityPayload,
)
from trading_agent.day_historical_evidence_models import DayHistoricalEvidenceSeal
from trading_agent.day_research_review_types import (
    DayExecutionAuthorityClass,
    DayExecutionEligibilityStatus,
    DayPromotionStatus,
    DayReviewEvidenceKind,
    DayReviewModel,
    InvalidDayResearchReviewError,
    day_review_content_id,
)
from trading_agent.intraday_overfit_diagnostics_models import IntradayOverfitDiagnosticsStatus
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import TerminalOutcome, aware

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ARTIFACT_REF = re.compile(r"^artifact://safe/[0-9a-f]{64}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_REASON = re.compile(r"^[a-z0-9][a-z0-9_]{0,127}$")


class DayReviewWindow(DayReviewModel):
    first_session_date: dt.date
    last_session_date: dt.date
    opened_at: dt.datetime
    closes_at: dt.datetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if (
            not aware(self.opened_at)
            or not aware(self.closes_at)
            or self.last_session_date < self.first_session_date
            or self.closes_at <= self.opened_at
        ):
            raise InvalidDayResearchReviewError("day_review_window_invalid")
        return self


class DayMarketEvidenceRef(DayReviewModel):
    kind: DayReviewEvidenceKind
    market_id: MarketId
    artifact_ref: str
    artifact_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_ref(self) -> Self:
        if (
            _SAFE_ARTIFACT_REF.fullmatch(self.artifact_ref) is None
            or self.artifact_ref != f"artifact://safe/{self.artifact_sha256}"
        ):
            raise InvalidDayResearchReviewError("day_review_evidence_ref_invalid")
        return self


class DaySelectionAdjustedStatistics(DayReviewModel):
    total_attempted_variants: int = Field(ge=1, le=10_000)
    deflated_sharpe_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    pbo_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    power_ci_sufficient: bool


class PromotionDecisionPayload(DayReviewModel):
    capsule_id: str = Field(pattern=_SHA256_PATTERN)
    hypothesis_version_id: str = Field(pattern=_SHA256_PATTERN)
    market_id: MarketId
    status: DayPromotionStatus
    review_window: DayReviewWindow
    historical_evidence_seal: DayHistoricalEvidenceSeal
    evidence_refs: tuple[DayMarketEvidenceRef, ...] = Field(min_length=5, max_length=5)
    attempted_variant_ids: tuple[str, ...] = Field(min_length=1, max_length=10_000)
    selection_adjusted_statistics: DaySelectionAdjustedStatistics
    blockers: tuple[str, ...]
    reviewer_version: str
    policy_version: str
    owner_approval_required: bool
    effective_after_session: dt.date
    decided_at: dt.datetime
    promotion_authority: Literal[False] = False
    paper_order_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        self._validate_status()
        seal = self.historical_evidence_seal.payload
        diagnostics = seal.selection_diagnostics
        statistics = self.selection_adjusted_statistics
        if not aware(self.decided_at) or self.decided_at < self.review_window.closes_at:
            raise InvalidDayResearchReviewError("day_review_window_open")
        if self.effective_after_session <= self.review_window.last_session_date:
            raise InvalidDayResearchReviewError("day_review_effective_session_invalid")
        if (
            seal.capsule_id != self.capsule_id
            or seal.hypothesis_version_id != self.hypothesis_version_id
            or seal.market_id is not self.market_id
            or any(reference.market_id is not self.market_id for reference in self.evidence_refs)
        ):
            raise InvalidDayResearchReviewError("day_review_market_or_lineage_mismatch")
        if tuple(reference.kind for reference in self.evidence_refs) != tuple(DayReviewEvidenceKind):
            raise InvalidDayResearchReviewError("day_review_evidence_refs_incomplete")
        if (
            self.attempted_variant_ids != diagnostics.input_attempt_ids
            or statistics.total_attempted_variants != seal.attempted_variant_count
            or statistics.total_attempted_variants != len(self.attempted_variant_ids)
            or statistics.deflated_sharpe_probability != diagnostics.deflated_sharpe_probability
            or statistics.pbo_probability != diagnostics.pbo_probability
        ):
            raise InvalidDayResearchReviewError("day_review_attempt_accounting_invalid")
        if (
            self.blockers != tuple(sorted(set(self.blockers)))
            or any(_REASON.fullmatch(reason) is None for reason in self.blockers)
            or _VERSION.fullmatch(self.reviewer_version) is None
            or _VERSION.fullmatch(self.policy_version) is None
        ):
            raise InvalidDayResearchReviewError("day_review_metadata_invalid")
        return self

    def _validate_status(self) -> None:
        match self.status:
            case DayPromotionStatus.REJECTED | DayPromotionStatus.INSUFFICIENT:
                if not self.blockers or self.owner_approval_required:
                    raise InvalidDayResearchReviewError("day_review_blocked_status_invalid")
            case DayPromotionStatus.SHADOW_CANDIDATE:
                if self.blockers or self.owner_approval_required:
                    raise InvalidDayResearchReviewError("day_review_shadow_status_invalid")
            case DayPromotionStatus.PAPER_TRIAL_CANDIDATE | DayPromotionStatus.PAPER_CHAMPION_CANDIDATE:
                if self.market_id is MarketId.KR_EQUITIES:
                    raise InvalidDayResearchReviewError("day_review_kr_shadow_ceiling")
                if self.blockers or not self.owner_approval_required:
                    raise InvalidDayResearchReviewError("day_review_owner_approval_required")
            case unreachable:
                assert_never(unreachable)


class PromotionDecision(DayReviewModel):
    decision_id: str = Field(pattern=_SHA256_PATTERN)
    payload: PromotionDecisionPayload

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.decision_id != day_review_content_id(self.payload):
            raise InvalidDayResearchReviewError("day_promotion_decision_identity_invalid")
        return self


class ReviewFeedbackSummary(DayReviewModel):
    decision_id: str = Field(pattern=_SHA256_PATTERN)
    capsule_id: str = Field(pattern=_SHA256_PATTERN)
    market_id: MarketId
    status: DayPromotionStatus
    classification: TerminalOutcome
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=8)
    selection_diagnostics_status: IntradayOverfitDiagnosticsStatus
    power_ci_sufficient: bool
    next_review_date: dt.date

    @model_validator(mode="after")
    def validate_feedback(self) -> Self:
        if self.reason_codes != tuple(sorted(set(self.reason_codes))) or any(
            _REASON.fullmatch(reason) is None for reason in self.reason_codes
        ):
            raise InvalidDayResearchReviewError("day_review_feedback_reason_invalid")
        return self


__all__ = (
    "DayExecutionAuthorityClass",
    "DayExecutionEligibilityStatus",
    "DayMarketEvidenceRef",
    "DayOwnerAuthorityEvent",
    "DayOwnerAuthorityEventPayload",
    "DayPromotionStatus",
    "DayReviewEvidenceKind",
    "DayReviewWindow",
    "DaySelectionAdjustedStatistics",
    "ExecutionEligibility",
    "ExecutionEligibilityPayload",
    "InvalidDayResearchReviewError",
    "PromotionDecision",
    "PromotionDecisionPayload",
    "ReviewFeedbackSummary",
)
