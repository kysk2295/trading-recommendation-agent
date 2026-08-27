from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import assert_never, override

from trading_agent.kr_autonomous_outcome_models import (
    KrAutonomousOutcomeMemory,
    KrLoopFailureCode,
    KrOutcomeExecutionState,
    KrOutcomeMarketEvidenceState,
)
from trading_agent.kr_autonomous_trade_models import KrCriticReason
from trading_agent.kr_loop_engineer_receipts import KrLoopShadowReceipt

_KST = dt.timezone(dt.timedelta(hours=9))


class InvalidKrLoopEvaluationError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR Loop evaluation evidence is invalid"


@dataclass(frozen=True, slots=True)
class KrLoopLaneEvaluation:
    score: Decimal
    error_count: int
    data_eligibility_failures: int
    order_mismatches: int
    research_task_losses: int
    evidence_refs: tuple[str, ...]


def score_outcomes(
    failure_code: KrLoopFailureCode,
    outcomes: tuple[KrAutonomousOutcomeMemory, ...],
    session_date: dt.date,
) -> KrLoopLaneEvaluation:
    selected = tuple(item for item in outcomes if item.observed_at.astimezone(_KST).date() == session_date)
    if not selected:
        raise InvalidKrLoopEvaluationError
    scores = tuple(_outcome_score(failure_code, item) for item in selected)
    return KrLoopLaneEvaluation(
        score=sum(scores, Decimal(0)) / Decimal(len(scores)),
        error_count=0,
        data_eligibility_failures=sum(
            item.market_evidence_state is not KrOutcomeMarketEvidenceState.CURRENT for item in selected
        ),
        order_mismatches=0,
        research_task_losses=sum(item.execution_state is KrOutcomeExecutionState.REJECTED for item in selected),
        evidence_refs=tuple(sorted(f"outcome:{item.outcome_id}" for item in selected))[:32],
    )


def build_shadow_receipt(
    *,
    failure_code: KrLoopFailureCode,
    session_date: dt.date,
    champion: tuple[KrAutonomousOutcomeMemory, ...],
    challenger: tuple[KrAutonomousOutcomeMemory, ...],
    observed_at: dt.datetime,
) -> KrLoopShadowReceipt:
    champion_score = score_outcomes(failure_code, champion, session_date)
    challenger_score = score_outcomes(failure_code, challenger, session_date)
    return KrLoopShadowReceipt(
        session_date=session_date,
        observed_at=observed_at,
        champion_score=champion_score.score,
        challenger_score=challenger_score.score,
        error_count=challenger_score.error_count,
        data_eligibility_failures=challenger_score.data_eligibility_failures,
        order_mismatches=challenger_score.order_mismatches,
        research_task_losses=challenger_score.research_task_losses,
        evidence_refs=tuple(sorted(set((*champion_score.evidence_refs, *challenger_score.evidence_refs))))[:32],
    )


def _outcome_score(failure_code: KrLoopFailureCode, outcome: KrAutonomousOutcomeMemory) -> Decimal:
    match failure_code:
        case KrLoopFailureCode.CRITIC_CHRONOLOGY:
            return Decimal(int(KrCriticReason.NONCAUSAL_PUBLICATION.value not in outcome.decision_reason_codes))
        case KrLoopFailureCode.CRITIC_CLUSTER_COUNT:
            return min(Decimal(len(outcome.independent_source_cluster_ids)) / Decimal(2), Decimal(1))
        case KrLoopFailureCode.MARKET_DATA:
            return Decimal(int(outcome.market_evidence_state is KrOutcomeMarketEvidenceState.CURRENT))
        case KrLoopFailureCode.VIRTUAL_CENSORED:
            return Decimal(int(outcome.execution_state is not KrOutcomeExecutionState.VIRTUAL_CENSORED))
        case KrLoopFailureCode.VIRTUAL_STOP:
            return _virtual_stop_score(outcome.execution_state)
        case unreachable:
            assert_never(unreachable)


def _virtual_stop_score(state: KrOutcomeExecutionState) -> Decimal:
    match state:
        case KrOutcomeExecutionState.VIRTUAL_TARGETED:
            return Decimal(1)
        case KrOutcomeExecutionState.VIRTUAL_STOPPED:
            return Decimal(0)
        case _:
            return Decimal("0.5")


__all__ = (
    "InvalidKrLoopEvaluationError",
    "KrLoopLaneEvaluation",
    "build_shadow_receipt",
    "score_outcomes",
)
