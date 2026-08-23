from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final, Literal, Self, override

from pydantic import Field, model_validator

from trading_agent.day_historical_evidence_models import DayHistoricalEvidenceSeal
from trading_agent.day_research_review import (
    DayExecutionSessionContext,
    build_execution_eligibility,
    seal_promotion_decision,
)
from trading_agent.day_research_review_models import (
    DayMarketEvidenceRef,
    DayReviewModel,
    DayReviewWindow,
    DaySelectionAdjustedStatistics,
    ExecutionEligibility,
    PromotionDecision,
    PromotionDecisionPayload,
)
from trading_agent.day_research_review_types import DayPromotionStatus, day_review_content_id
from trading_agent.intraday_overfit_diagnostics_models import IntradayOverfitDiagnosticsStatus
from trading_agent.kr_day_capsule_outcomes import (
    KrDayCapsuleOutcome,
    KrDayCapsuleTerminalKind,
)
from trading_agent.research_identity_models import MarketId

CURRENT_KR_DAY_CAPSULE_REVIEWER_VERSION: Final = "kr_day_capsule_reviewer_v1"
CURRENT_KR_DAY_CAPSULE_POLICY_VERSION: Final = "kr_day_capsule_shadow_ceiling_v1"
MINIMUM_FORWARD_SESSIONS: Final = 20
MINIMUM_COMPLETED_TRADES: Final = 30


class InvalidKrDayCapsuleReviewError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day capsule review evidence is invalid"


class KrDayCapsuleReviewSealPayload(DayReviewModel):
    capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypothesis_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    market_id: Literal[MarketId.KR_EQUITIES]
    review_window: DayReviewWindow
    attempt_ids: tuple[str, ...] = Field(min_length=1, max_length=10_000)
    sealed_at: dt.datetime

    @model_validator(mode="after")
    def validate_preregistration(self) -> Self:
        if (
            self.sealed_at.tzinfo is None
            or self.sealed_at.utcoffset() is None
            or self.sealed_at > self.review_window.opened_at
            or self.attempt_ids != tuple(sorted(set(self.attempt_ids)))
            or any(not attempt_id or attempt_id != attempt_id.strip() for attempt_id in self.attempt_ids)
        ):
            raise InvalidKrDayCapsuleReviewError
        return self


class KrDayCapsuleReviewSeal(DayReviewModel):
    seal_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: KrDayCapsuleReviewSealPayload

    @classmethod
    def seal(cls, payload: KrDayCapsuleReviewSealPayload) -> KrDayCapsuleReviewSeal:
        return cls(seal_id=day_review_content_id(payload), payload=payload)

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.seal_id != day_review_content_id(self.payload):
            raise InvalidKrDayCapsuleReviewError
        return self


class KrDayCapsuleReviewRequest(DayReviewModel):
    capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypothesis_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    market_id: MarketId
    review_window: DayReviewWindow
    review_seal: KrDayCapsuleReviewSeal
    historical_evidence_seal: DayHistoricalEvidenceSeal
    evidence_refs: tuple[DayMarketEvidenceRef, ...] = Field(min_length=5, max_length=5)
    outcomes: tuple[KrDayCapsuleOutcome, ...] = Field(min_length=1, max_length=10_000)
    selection_adjusted_statistics: DaySelectionAdjustedStatistics
    effective_after_session: dt.date
    decided_at: dt.datetime
    session_context: DayExecutionSessionContext | None

    @model_validator(mode="after")
    def validate_structural_identity(self) -> Self:
        if len({outcome.outcome_id for outcome in self.outcomes}) != len(self.outcomes):
            raise InvalidKrDayCapsuleReviewError
        return self


@dataclass(frozen=True, slots=True)
class KrDayCapsuleReviewCounts:
    total_attempts: int
    completed_trades: int
    no_signal_attempts: int
    blocked_attempts: int
    failed_attempts: int
    censored_attempts: int
    completed_sessions: int


@dataclass(frozen=True, slots=True)
class KrDayCapsuleReviewResult:
    decision: PromotionDecision
    eligibility: ExecutionEligibility | None
    counts: KrDayCapsuleReviewCounts


def review_kr_day_capsule(
    request: KrDayCapsuleReviewRequest,
    *,
    project_eligibility: bool = True,
) -> KrDayCapsuleReviewResult:
    try:
        checked = KrDayCapsuleReviewRequest.model_validate(request.model_dump(mode="python"))
        _require_exact_dossier(checked)
        counts = _counts(checked.outcomes)
        status, blockers = _decision(checked, counts)
        payload = PromotionDecisionPayload(
            capsule_id=checked.capsule_id,
            hypothesis_version_id=checked.hypothesis_version_id,
            market_id=MarketId.KR_EQUITIES,
            status=status,
            review_window=checked.review_window,
            historical_evidence_seal=checked.historical_evidence_seal,
            evidence_refs=checked.evidence_refs,
            attempted_variant_ids=tuple(outcome.attempt_id for outcome in checked.outcomes),
            selection_adjusted_statistics=checked.selection_adjusted_statistics,
            blockers=blockers,
            reviewer_version=CURRENT_KR_DAY_CAPSULE_REVIEWER_VERSION,
            policy_version=CURRENT_KR_DAY_CAPSULE_POLICY_VERSION,
            owner_approval_required=False,
            effective_after_session=checked.effective_after_session,
            decided_at=checked.decided_at,
        )
        decision = seal_promotion_decision(payload)
        if project_eligibility:
            if checked.session_context is None:
                raise InvalidKrDayCapsuleReviewError
            eligibility = build_execution_eligibility(decision, checked.session_context)
        else:
            eligibility = None
        return KrDayCapsuleReviewResult(decision, eligibility, counts)
    except (AttributeError, TypeError, ValueError):
        raise InvalidKrDayCapsuleReviewError from None


def _require_exact_dossier(request: KrDayCapsuleReviewRequest) -> None:
    seal = request.historical_evidence_seal.payload
    diagnostics = seal.selection_diagnostics
    attempt_ids = tuple(outcome.attempt_id for outcome in request.outcomes)
    if (
        request.market_id is not MarketId.KR_EQUITIES
        or request.decided_at < request.review_window.closes_at
        or request.effective_after_session <= request.review_window.last_session_date
        or seal.market_id is not MarketId.KR_EQUITIES
        or seal.capsule_id != request.capsule_id
        or seal.hypothesis_version_id != request.hypothesis_version_id
        or request.review_seal.payload.capsule_id != request.capsule_id
        or request.review_seal.payload.hypothesis_version_id != request.hypothesis_version_id
        or request.review_seal.payload.market_id is not MarketId.KR_EQUITIES
        or request.review_seal.payload.review_window != request.review_window
        or request.review_seal.payload.attempt_ids != attempt_ids
        or len({outcome.trial_id for outcome in request.outcomes}) != len(request.outcomes)
        or any(reference.market_id is not MarketId.KR_EQUITIES for reference in request.evidence_refs)
        or any(
            outcome.market_id != MarketId.KR_EQUITIES.value
            or outcome.capsule_id != request.capsule_id
            or outcome.hypothesis_version_id != request.hypothesis_version_id
            or not request.review_window.first_session_date
            <= outcome.session_date
            <= request.review_window.last_session_date
            for outcome in request.outcomes
        )
        or attempt_ids != diagnostics.input_attempt_ids
        or len(attempt_ids) != seal.attempted_variant_count
        or request.selection_adjusted_statistics.total_attempted_variants != len(attempt_ids)
        or request.selection_adjusted_statistics.deflated_sharpe_probability
        != diagnostics.deflated_sharpe_probability
        or request.selection_adjusted_statistics.pbo_probability != diagnostics.pbo_probability
    ):
        raise InvalidKrDayCapsuleReviewError


def _counts(outcomes: tuple[KrDayCapsuleOutcome, ...]) -> KrDayCapsuleReviewCounts:
    kinds = tuple(outcome.kind for outcome in outcomes)
    completed_sessions = len({outcome.session_date for outcome in outcomes})
    return KrDayCapsuleReviewCounts(
        total_attempts=len(outcomes),
        completed_trades=kinds.count(KrDayCapsuleTerminalKind.EXIT),
        no_signal_attempts=kinds.count(KrDayCapsuleTerminalKind.NO_SIGNAL),
        blocked_attempts=kinds.count(KrDayCapsuleTerminalKind.BLOCKED),
        failed_attempts=kinds.count(KrDayCapsuleTerminalKind.FAILED),
        censored_attempts=kinds.count(KrDayCapsuleTerminalKind.CENSORED),
        completed_sessions=completed_sessions,
    )


def _decision(
    request: KrDayCapsuleReviewRequest,
    counts: KrDayCapsuleReviewCounts,
) -> tuple[DayPromotionStatus, tuple[str, ...]]:
    if counts.failed_attempts:
        return DayPromotionStatus.REJECTED, ("failed_attempts_present",)
    insufficient = (
        counts.censored_attempts > 0
        or counts.blocked_attempts > 0
        or counts.no_signal_attempts > 0
        or counts.completed_sessions < MINIMUM_FORWARD_SESSIONS
        or counts.completed_trades < MINIMUM_COMPLETED_TRADES
        or not request.selection_adjusted_statistics.power_ci_sufficient
        or request.historical_evidence_seal.payload.selection_diagnostics.status
        is not IntradayOverfitDiagnosticsStatus.DIAGNOSTIC_READY
    )
    if insufficient:
        return DayPromotionStatus.INSUFFICIENT, ("fixed_window_evidence_insufficient",)
    return DayPromotionStatus.SHADOW_CANDIDATE, ()


__all__ = (
    "CURRENT_KR_DAY_CAPSULE_POLICY_VERSION",
    "CURRENT_KR_DAY_CAPSULE_REVIEWER_VERSION",
    "InvalidKrDayCapsuleReviewError",
    "KrDayCapsuleReviewCounts",
    "KrDayCapsuleReviewRequest",
    "KrDayCapsuleReviewResult",
    "KrDayCapsuleReviewSeal",
    "KrDayCapsuleReviewSealPayload",
    "review_kr_day_capsule",
)
