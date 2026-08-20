from __future__ import annotations

import datetime as dt
import hashlib

from tests.test_day_historical_evidence import NOW, SHA_A, SHA_B, SHA_C, _seal
from trading_agent.day_research_review import DayExecutionSessionContext
from trading_agent.day_research_review_models import (
    DayExecutionAuthorityClass,
    DayMarketEvidenceRef,
    DayOwnerAuthorityEvent,
    DayOwnerAuthorityEventPayload,
    DayReviewEvidenceKind,
    DayReviewWindow,
    DaySelectionAdjustedStatistics,
    ExecutionEligibilityPayload,
    PromotionDecision,
    PromotionDecisionPayload,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.intraday_promotion_models import DayPromotionStatus
from trading_agent.research_identity_models import MarketId


def review_window() -> DayReviewWindow:
    return DayReviewWindow(
        first_session_date=dt.date(2026, 8, 3),
        last_session_date=dt.date(2026, 8, 19),
        opened_at=NOW - dt.timedelta(days=17),
        closes_at=NOW,
    )


def evidence_refs(
    market_id: MarketId = MarketId.US_EQUITIES,
) -> tuple[DayMarketEvidenceRef, ...]:
    return tuple(
        DayMarketEvidenceRef(
            kind=kind,
            market_id=market_id,
            artifact_ref=f"artifact://safe/{value}",
            artifact_sha256=value,
        )
        for kind, value in zip(DayReviewEvidenceKind, (SHA_A, SHA_B, SHA_C, "d" * 64, "e" * 64), strict=True)
    )


def promotion_payload(
    *,
    market_id: MarketId = MarketId.US_EQUITIES,
    status: DayPromotionStatus = DayPromotionStatus.PAPER_TRIAL_CANDIDATE,
    decided_at: dt.datetime = NOW,
) -> PromotionDecisionPayload:
    seal = _seal(market_id)
    return PromotionDecisionPayload(
        capsule_id=seal.payload.capsule_id,
        hypothesis_version_id=seal.payload.hypothesis_version_id,
        market_id=market_id,
        status=status,
        review_window=review_window(),
        historical_evidence_seal=seal,
        evidence_refs=evidence_refs(market_id),
        attempted_variant_ids=seal.payload.selection_diagnostics.input_attempt_ids,
        selection_adjusted_statistics=DaySelectionAdjustedStatistics(
            total_attempted_variants=seal.payload.attempted_variant_count,
            deflated_sharpe_probability=seal.payload.selection_diagnostics.deflated_sharpe_probability,
            pbo_probability=seal.payload.selection_diagnostics.pbo_probability,
            power_ci_sufficient=True,
        ),
        blockers=(),
        reviewer_version="day-reviewer-v1",
        policy_version="day-promotion-policy-v1",
        owner_approval_required=status
        in {
            DayPromotionStatus.PAPER_TRIAL_CANDIDATE,
            DayPromotionStatus.PAPER_CHAMPION_CANDIDATE,
        },
        effective_after_session=dt.date(2026, 8, 20),
        decided_at=decided_at,
    )


def content_id(
    payload: PromotionDecisionPayload | DayOwnerAuthorityEventPayload | ExecutionEligibilityPayload,
) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()


def decision(
    *,
    market_id: MarketId = MarketId.US_EQUITIES,
    status: DayPromotionStatus = DayPromotionStatus.PAPER_TRIAL_CANDIDATE,
) -> PromotionDecision:
    payload = promotion_payload(market_id=market_id, status=status)
    return PromotionDecision(decision_id=content_id(payload), payload=payload)


def authority_event(review_decision: PromotionDecision) -> DayOwnerAuthorityEvent:
    payload = DayOwnerAuthorityEventPayload(
        decision_id=review_decision.decision_id,
        capsule_id=review_decision.payload.capsule_id,
        hypothesis_version_id=review_decision.payload.hypothesis_version_id,
        market_id=review_decision.payload.market_id,
        authority_class=DayExecutionAuthorityClass.PAPER_TRIAL_APPROVED,
        owner_id="owner_1",
        approved_at=NOW + dt.timedelta(minutes=1),
        effective_after_session=review_decision.payload.effective_after_session,
    )
    return DayOwnerAuthorityEvent(authority_event_id=content_id(payload), payload=payload)


def session_context(review_decision: PromotionDecision) -> DayExecutionSessionContext:
    return DayExecutionSessionContext(
        session_date=review_decision.payload.effective_after_session,
        sequence=1,
        previous_event_id=None,
        clean_commit_sha256=SHA_A,
        risk_policy_sha256=SHA_B,
        effective_at=NOW + dt.timedelta(minutes=2),
        expires_at=NOW + dt.timedelta(hours=8),
    )
