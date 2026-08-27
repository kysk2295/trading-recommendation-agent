from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from trading_agent.kr_autonomous_outcome_models import (
    KrAutonomousOutcomeMemory,
    KrLoopFailureCode,
    KrOutcomeExecutionState,
    KrOutcomeMarketEvidenceState,
    KrOutcomeSessionPhase,
    kr_autonomous_outcome_id,
)
from trading_agent.kr_autonomous_trade_models import KrAutonomousTradeOutcome
from trading_agent.kr_loop_evaluation import InvalidKrLoopEvaluationError, build_shadow_receipt, score_outcomes
from trading_agent.kr_social_signal_models import KrSocialVerificationState

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 28, 15, 40, tzinfo=KST)


def test_cluster_evaluation_scores_same_session_actual_outcomes() -> None:
    champion = (_outcome("1", clusters=1),)
    challenger = (_outcome("2", clusters=3),)

    receipt = build_shadow_receipt(
        failure_code=KrLoopFailureCode.CRITIC_CLUSTER_COUNT,
        session_date=NOW.date(),
        champion=champion,
        challenger=challenger,
        observed_at=NOW,
    )

    assert receipt.champion_score == Decimal("0.5")
    assert receipt.challenger_score == Decimal("1")
    assert receipt.data_eligibility_failures == 0
    assert receipt.order_mismatches == 0
    assert receipt.research_task_losses == 0
    assert receipt.evidence_refs == tuple(
        sorted((f"outcome:{champion[0].outcome_id}", f"outcome:{challenger[0].outcome_id}"))
    )


def test_evaluation_refuses_missing_or_cross_session_evidence() -> None:
    with pytest.raises(InvalidKrLoopEvaluationError):
        _ = score_outcomes(KrLoopFailureCode.MARKET_DATA, (), NOW.date())
    with pytest.raises(InvalidKrLoopEvaluationError):
        _ = score_outcomes(
            KrLoopFailureCode.MARKET_DATA,
            (_outcome("3", clusters=2, observed_at=NOW - dt.timedelta(days=1)),),
            NOW.date(),
        )


def _outcome(
    marker: str,
    *,
    clusters: int,
    observed_at: dt.datetime = NOW,
    market: KrOutcomeMarketEvidenceState = KrOutcomeMarketEvidenceState.CURRENT,
) -> KrAutonomousOutcomeMemory:
    draft = KrAutonomousOutcomeMemory.model_construct(
        outcome_id="",
        task_id=marker * 64,
        trade_event_id=(str(int(marker) + 1)) * 64,
        position_event_id=(str(int(marker) + 2)) * 64,
        trade_outcome=KrAutonomousTradeOutcome.RECOMMEND,
        execution_state=KrOutcomeExecutionState.VIRTUAL_TARGETED,
        symbol="005930",
        theme="반도체",
        verification_state=KrSocialVerificationState.MULTI_SOURCE_CORROBORATED,
        independent_source_count=max(1, clusters),
        independent_source_cluster_ids=tuple(f"cluster:{index}" for index in range(clusters)),
        decision_reason_codes=(),
        market_evidence_state=market,
        session_phase=KrOutcomeSessionPhase.CLOSING,
        price_levels=None,
        horizons=(),
        evidence_refs=(f"evidence:{marker}",),
        lineage_sha256="a" * 64,
        observed_at=observed_at,
    )
    return KrAutonomousOutcomeMemory.model_validate(
        draft.model_copy(update={"outcome_id": kr_autonomous_outcome_id(draft)}).model_dump(mode="python")
    )


__all__ = ("NOW", "_outcome")
