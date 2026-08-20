from __future__ import annotations

import datetime as dt
import importlib
from importlib.util import find_spec

import pytest
from pydantic import ValidationError

from tests.day_research_review_support import (
    authority_event,
    content_id,
    decision,
    evidence_refs,
    promotion_payload,
)
from tests.test_day_historical_evidence import NOW, SHA_A, SHA_B, _seal
from trading_agent.day_research_review_models import (
    DayExecutionEligibilityStatus,
    ExecutionEligibility,
    ExecutionEligibilityPayload,
    PromotionDecisionPayload,
    ReviewFeedbackSummary,
)
from trading_agent.intraday_promotion_models import DayPromotionStatus
from trading_agent.research_identity_models import MarketId


def test_day_review_models_are_exposed_from_the_promotion_contract() -> None:
    # Given: the existing intraday promotion module.
    module = importlib.import_module("trading_agent.intraday_promotion_models")

    # When: its market-generic Day review contract is inspected.
    names = {
        "DayExecutionEligibilityStatus",
        "DayOwnerAuthorityEvent",
        "DayReviewWindow",
        "ExecutionEligibility",
        "PromotionDecision",
        "ReviewFeedbackSummary",
    }

    # Then: promotion evidence and per-session authority artifacts are explicit.
    assert names <= set(module.__all__)


def test_day_review_builder_and_ledger_module_exists() -> None:
    # Given: the market-generic Day review model contract.
    module_name = "trading_agent.day_research_review"

    # When: its orchestration and persistence surface is resolved.
    module = find_spec(module_name)

    # Then: review artifacts have one concrete construction and ledger boundary.
    assert module is not None


def test_day_review_builder_and_ledger_api_is_explicit() -> None:
    # Given: the concrete Day review orchestration module.
    module = importlib.import_module("trading_agent.day_research_review")

    # When: its supported construction and persistence surface is inspected.
    names = {
        "DayExecutionSessionContext",
        "append_execution_eligibility",
        "build_execution_eligibility",
        "build_review_feedback",
        "record_promotion_decision",
        "seal_owner_authority_event",
        "seal_promotion_decision",
    }

    # Then: callers do not need to assemble authority artifacts ad hoc.
    assert names <= set(module.__all__)


def test_day_review_reader_api_is_explicit() -> None:
    # Given: the Day experiment ledger reader module.
    module = importlib.import_module("trading_agent.day_research_ledger_reader")

    # When: review and session-eligibility projections are inspected.
    names = {"day_execution_eligibility_events", "day_promotion_decisions"}

    # Then: restart-safe reads are part of the ledger boundary.
    assert names <= set(dir(module))


def test_fixed_review_window_cannot_close_early() -> None:
    # Given: a promotion payload whose fixed review window is still open.
    payload = promotion_payload().model_dump(mode="python")

    # When / Then: constructing the decision payload before close fails closed.
    with pytest.raises(ValidationError, match="review_window_open"):
        _ = PromotionDecisionPayload.model_validate(payload | {"decided_at": NOW - dt.timedelta(seconds=1)})


def test_promotion_counts_every_attempted_variant() -> None:
    # Given: a sealed evidence record containing three attempted variants.
    payload = promotion_payload().model_dump(mode="python")

    # When / Then: omitting an attempted variant from review is rejected.
    with pytest.raises(ValidationError, match="attempt"):
        _ = PromotionDecisionPayload.model_validate(payload | {"attempted_variant_ids": ("attempt-1", "attempt-2")})


def test_promotion_rejects_mixed_market_evidence() -> None:
    # Given: a US review with one KR evidence reference.
    refs = list(evidence_refs())
    refs[-1] = refs[-1].model_copy(update={"market_id": MarketId.KR_EQUITIES})
    payload = promotion_payload().model_dump(mode="python")

    # When / Then: market-crossed evidence cannot enter the decision.
    with pytest.raises(ValidationError, match="market"):
        _ = PromotionDecisionPayload.model_validate(payload | {"evidence_refs": tuple(refs)})


@pytest.mark.parametrize(
    "status",
    (
        DayPromotionStatus.PAPER_TRIAL_CANDIDATE,
        DayPromotionStatus.PAPER_CHAMPION_CANDIDATE,
    ),
)
def test_kr_promotion_cannot_exceed_shadow_candidate(status: DayPromotionStatus) -> None:
    # Given: a Korean-market evidence seal proposed for a Paper candidate state.
    seal = _seal(MarketId.KR_EQUITIES)
    payload = promotion_payload().model_dump(mode="python")

    # When / Then: the read-only broker boundary caps KR at Shadow candidate.
    with pytest.raises(ValidationError, match="kr_shadow_ceiling"):
        _ = PromotionDecisionPayload.model_validate(
            payload
            | {
                "market_id": MarketId.KR_EQUITIES,
                "status": status,
                "historical_evidence_seal": seal,
                "evidence_refs": evidence_refs(MarketId.KR_EQUITIES),
            }
        )


def test_us_paper_candidate_still_has_no_order_authority() -> None:
    # Given: a complete US Paper trial candidate decision payload.
    payload = promotion_payload()

    # When: its authority fields are inspected.
    authority = (payload.owner_approval_required, payload.paper_order_authority)

    # Then: promotion only requests owner approval and never grants an order path.
    assert authority == (True, False)


def test_promotion_decision_is_content_addressed_and_cannot_grant_orders() -> None:
    # Given: a validated evidence-only promotion payload.
    review_decision = decision()

    # When: its immutable identity and authority are inspected.
    result = (review_decision.decision_id, review_decision.payload.paper_order_authority)

    # Then: the identity seals the payload while order authority remains false.
    assert result == (content_id(review_decision.payload), False)


def test_review_feedback_structurally_redacts_holdout_and_provider_details() -> None:
    # Given: a feedback summary derived from an evidence-only review.
    review_decision = decision()
    summary = ReviewFeedbackSummary(
        decision_id=review_decision.decision_id,
        capsule_id=review_decision.payload.capsule_id,
        market_id=review_decision.payload.market_id,
        status=review_decision.payload.status,
        classification=review_decision.payload.historical_evidence_seal.payload.classification,
        reason_codes=("review_passed",),
        selection_diagnostics_status=(
            review_decision.payload.historical_evidence_seal.payload.selection_diagnostics.status
        ),
        power_ci_sufficient=True,
        next_review_date=dt.date(2026, 8, 21),
    )

    # When: the generator-facing schema and JSON are inspected.
    fields = set(ReviewFeedbackSummary.model_fields)
    serialized = summary.model_dump_json()

    # Then: exact holdout, symbol, account, and provider/auth data have no field path.
    assert (
        not {
            "exact_metrics",
            "holdout_values",
            "symbol_contributions",
            "account_id",
            "provider_payload",
            "auth_data",
        }
        & fields
    )
    assert all(token not in serialized for token in ("0.91", "0.12", "authorization"))


def test_us_eligibility_requires_a_matching_owner_authority_event() -> None:
    # Given: a US Paper candidate with no owner authority event.
    review_decision = decision()
    payload = ExecutionEligibilityPayload(
        decision_id=review_decision.decision_id,
        capsule_id=review_decision.payload.capsule_id,
        hypothesis_version_id=review_decision.payload.hypothesis_version_id,
        market_id=review_decision.payload.market_id,
        session_date=review_decision.payload.effective_after_session,
        sequence=1,
        previous_event_id=None,
        clean_commit_sha256=SHA_A,
        risk_policy_sha256=SHA_B,
        authority_event=None,
        effective_at=NOW + dt.timedelta(minutes=2),
        expires_at=NOW + dt.timedelta(hours=8),
        status=DayExecutionEligibilityStatus.ELIGIBLE,
        broker_blocked=False,
        blockers=(),
        paper_order_authority=True,
    )

    # When / Then: an eligible order path without owner authority is rejected.
    with pytest.raises(ValidationError, match="owner_authority"):
        _ = ExecutionEligibility(
            eligibility_event_id=content_id(payload),
            payload=payload,
        )


def test_matching_owner_event_supports_us_session_eligibility() -> None:
    # Given: a US Paper candidate and its distinct owner authority event.
    review_decision = decision()
    authority = authority_event(review_decision)
    payload = ExecutionEligibilityPayload(
        decision_id=review_decision.decision_id,
        capsule_id=review_decision.payload.capsule_id,
        hypothesis_version_id=review_decision.payload.hypothesis_version_id,
        market_id=review_decision.payload.market_id,
        session_date=review_decision.payload.effective_after_session,
        sequence=1,
        previous_event_id=None,
        clean_commit_sha256=SHA_A,
        risk_policy_sha256=SHA_B,
        authority_event=authority,
        effective_at=NOW + dt.timedelta(minutes=2),
        expires_at=NOW + dt.timedelta(hours=8),
        status=DayExecutionEligibilityStatus.ELIGIBLE,
        broker_blocked=False,
        blockers=(),
        paper_order_authority=True,
    )

    # When: the session eligibility artifact is sealed.
    eligibility = ExecutionEligibility(
        eligibility_event_id=content_id(payload),
        payload=payload,
    )

    # Then: authority is exact, session-bound, expiring, and separate from promotion.
    assert eligibility.payload.authority_event == authority
    assert eligibility.payload.expires_at > eligibility.payload.effective_at
    assert review_decision.payload.paper_order_authority is False


def test_kr_execution_eligibility_is_explicitly_broker_blocked() -> None:
    # Given: a Korean Shadow candidate and a broker-blocked session artifact.
    review_decision = decision(
        market_id=MarketId.KR_EQUITIES,
        status=DayPromotionStatus.SHADOW_CANDIDATE,
    )
    payload = ExecutionEligibilityPayload(
        decision_id=review_decision.decision_id,
        capsule_id=review_decision.payload.capsule_id,
        hypothesis_version_id=review_decision.payload.hypothesis_version_id,
        market_id=review_decision.payload.market_id,
        session_date=review_decision.payload.effective_after_session,
        sequence=1,
        previous_event_id=None,
        clean_commit_sha256=SHA_A,
        risk_policy_sha256=SHA_B,
        authority_event=None,
        effective_at=NOW + dt.timedelta(minutes=2),
        expires_at=NOW + dt.timedelta(hours=8),
        status=DayExecutionEligibilityStatus.BLOCKED,
        broker_blocked=True,
        blockers=("provider_read_only",),
        paper_order_authority=False,
    )

    # When: the eligibility artifact is sealed.
    eligibility = ExecutionEligibility(
        eligibility_event_id=content_id(payload),
        payload=payload,
    )

    # Then: KR remains an auditable Shadow result with no order authority.
    assert eligibility.payload.broker_blocked is True
    assert eligibility.payload.paper_order_authority is False
