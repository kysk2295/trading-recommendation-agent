from __future__ import annotations

import datetime as dt

import pytest

from tests.day_research_review_support import evidence_refs, review_window, session_context
from tests.test_day_historical_evidence import NOW, _seal
from trading_agent.day_research_review_models import DaySelectionAdjustedStatistics
from trading_agent.day_research_review_types import (
    DayExecutionEligibilityStatus,
    DayPromotionStatus,
)
from trading_agent.kr_day_capsule_outcomes import (
    KrDayCapsuleOutcome,
    KrDayCapsuleOutcomeFields,
    KrDayCapsuleTerminalKind,
)
from trading_agent.kr_day_capsule_reviewer import (
    InvalidKrDayCapsuleReviewError,
    KrDayCapsuleReviewRequest,
    KrDayCapsuleReviewSeal,
    KrDayCapsuleReviewSealPayload,
    review_kr_day_capsule,
)
from trading_agent.research_identity_models import MarketId


def test_review_counts_every_terminal_attempt_and_keeps_kr_read_only() -> None:
    # Given: the exact KR seal attempt set, including failures and censored/no-signal/blocked attempts.
    request = _request()

    # When: the fixed-window review is projected.
    result = review_kr_day_capsule(request)

    # Then: every attempted version is counted and authority remains blocked.
    assert result.eligibility is not None
    assert result.counts.total_attempts == 5
    assert result.counts.failed_attempts == 1
    assert result.counts.censored_attempts == 1
    assert result.counts.no_signal_attempts == 1
    assert result.counts.blocked_attempts == 1
    assert result.decision.payload.status is DayPromotionStatus.REJECTED
    assert result.eligibility.payload.status is DayExecutionEligibilityStatus.BLOCKED
    assert result.eligibility.payload.blockers == ("provider_read_only",)
    assert result.eligibility.payload.authority_event is None
    assert result.eligibility.payload.paper_order_authority is False


def test_review_rejects_shortened_window_mixed_market_and_missing_attempt() -> None:
    # Given: an exact KR request and three protected-boundary mutations.
    request = _request()
    shortened_window = request.review_window.model_copy(
        update={
            "last_session_date": request.review_window.last_session_date - dt.timedelta(days=1),
            "closes_at": request.review_window.closes_at - dt.timedelta(days=1),
        }
    )
    shortened = request.model_copy(update={"review_window": shortened_window})
    mixed = request.model_copy(update={"evidence_refs": evidence_refs(MarketId.US_EQUITIES)})
    missing = request.model_copy(update={"outcomes": request.outcomes[:-1]})

    # When / Then: no mutation can manufacture an eligible review dossier.
    for invalid in (shortened, mixed, missing):
        with pytest.raises(InvalidKrDayCapsuleReviewError):
            _ = review_kr_day_capsule(invalid)


def test_passing_review_is_capped_at_shadow_candidate() -> None:
    # Given: sufficient clean KR terminal attempts and ready selection statistics.
    request = _request(clean_attempts=30)

    # When: every comparison minimum is satisfied.
    result = review_kr_day_capsule(request)

    # Then: the highest possible status is Shadow candidate and remains provider-read-only.
    assert result.eligibility is not None
    assert result.decision.payload.status is DayPromotionStatus.SHADOW_CANDIDATE
    assert result.eligibility.payload.status is DayExecutionEligibilityStatus.BLOCKED
    assert result.eligibility.payload.broker_blocked is True


def _request(*, clean_attempts: int | None = None) -> KrDayCapsuleReviewRequest:
    seal = _seal(MarketId.KR_EQUITIES)
    attempt_ids = seal.payload.selection_diagnostics.input_attempt_ids
    selected_window = review_window()
    first_session = selected_window.first_session_date
    if clean_attempts is None:
        ids = (*attempt_ids, "attempt-4", "attempt-5")
        diagnostics = seal.payload.selection_diagnostics.model_copy(
            update={"input_attempt_ids": ids, "total_attempted_variants": len(ids)}
        )
        payload = seal.payload.model_copy(
            update={"attempted_variant_count": len(ids), "selection_diagnostics": diagnostics}
        )
        seal = seal.__class__(seal_id=payload.content_sha256, payload=payload)
        kinds = (
            KrDayCapsuleTerminalKind.EXIT,
            KrDayCapsuleTerminalKind.FAILED,
            KrDayCapsuleTerminalKind.CENSORED,
            KrDayCapsuleTerminalKind.NO_SIGNAL,
            KrDayCapsuleTerminalKind.BLOCKED,
        )
    else:
        first_session = dt.date(2026, 7, 20)
        selected_window = selected_window.model_copy(
            update={"first_session_date": first_session, "opened_at": NOW - dt.timedelta(days=31)}
        )
        ids = tuple(f"attempt-{index:02d}" for index in range(clean_attempts))
        diagnostics = seal.payload.selection_diagnostics.model_copy(
            update={"input_attempt_ids": ids, "total_attempted_variants": clean_attempts}
        )
        payload = seal.payload.model_copy(
            update={"attempted_variant_count": clean_attempts, "selection_diagnostics": diagnostics}
        )
        seal = seal.__class__(seal_id=payload.content_sha256, payload=payload)
        kinds = (KrDayCapsuleTerminalKind.EXIT,) * clean_attempts
    outcomes = tuple(
        _outcome(attempt_id, kind, index, first_session)
        for index, (attempt_id, kind) in enumerate(zip(ids, kinds, strict=True))
    )
    statistics = DaySelectionAdjustedStatistics(
        total_attempted_variants=len(ids),
        deflated_sharpe_probability=seal.payload.selection_diagnostics.deflated_sharpe_probability,
        pbo_probability=seal.payload.selection_diagnostics.pbo_probability,
        power_ci_sufficient=True,
    )
    seal_payload = KrDayCapsuleReviewSealPayload(
        capsule_id=seal.payload.capsule_id,
        hypothesis_version_id=seal.payload.hypothesis_version_id,
        market_id=MarketId.KR_EQUITIES,
        review_window=selected_window,
        attempt_ids=ids,
        sealed_at=selected_window.opened_at,
    )
    base = KrDayCapsuleReviewRequest(
        capsule_id=seal.payload.capsule_id,
        hypothesis_version_id=seal.payload.hypothesis_version_id,
        market_id=MarketId.KR_EQUITIES,
        review_window=selected_window,
        review_seal=KrDayCapsuleReviewSeal.seal(seal_payload),
        historical_evidence_seal=seal,
        evidence_refs=evidence_refs(MarketId.KR_EQUITIES),
        outcomes=outcomes,
        selection_adjusted_statistics=statistics,
        effective_after_session=dt.date(2026, 8, 20),
        decided_at=NOW,
        session_context=None,
    )
    provisional = review_kr_day_capsule(base, project_eligibility=False)
    return base.model_copy(update={"session_context": session_context(provisional.decision)})


def _outcome(
    attempt_id: str,
    kind: KrDayCapsuleTerminalKind,
    index: int,
    first_session: dt.date,
) -> KrDayCapsuleOutcome:
    payload: KrDayCapsuleOutcomeFields = {
        "attempt_id": attempt_id,
        "capsule_id": "a" * 64,
        "hypothesis_version_id": "b" * 64,
        "trial_id": f"trial-{index}",
        "session_date": first_session + dt.timedelta(days=index),
        "kind": kind,
        "reason": kind.value,
        "terminal_event_id": f"{index + 1:064x}",
        "net_return": 0 if kind is KrDayCapsuleTerminalKind.EXIT else None,
        "realized_r": 0 if kind is KrDayCapsuleTerminalKind.EXIT else None,
    }
    return KrDayCapsuleOutcome.seal(payload)
