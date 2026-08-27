from __future__ import annotations

from decimal import Decimal
from typing import Final

from trading_agent.kr_loop_engineer_models import (
    KrLoopCandidateSnapshot,
    KrLoopHealthReceipt,
    KrLoopValidationReceipt,
)

_PROMOTION_MARGIN: Final = Decimal("0.05")
_MAX_ERROR_RATE: Final = Decimal("0.05")


def validation_reasons(
    candidate: KrLoopCandidateSnapshot,
    receipt: KrLoopValidationReceipt,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if receipt.candidate_id != candidate.candidate_id or receipt.candidate_commit != candidate.candidate_commit:
        reasons.append("validation_lineage_mismatch")
    checks = (
        (receipt.pytest_passed, "pytest_failed"),
        (receipt.ruff_passed, "ruff_failed"),
        (receipt.basedpyright_passed, "basedpyright_failed"),
        (receipt.manual_qa_passed, "manual_qa_failed"),
        (receipt.replay_passed, "replay_failed"),
        (receipt.lookahead_violations == 0, "lookahead_violation"),
        (receipt.broker_mutations == 0, "broker_mutation_detected"),
    )
    reasons.extend(reason for passed, reason in checks if not passed)
    return tuple(sorted(reasons))


def promotion_blocked(candidate: KrLoopCandidateSnapshot) -> bool:
    receipts = candidate.shadow_receipts
    return (
        len({item.session_date for item in receipts}) < 2
        or any(item.session_date <= candidate.created_at.date() for item in receipts)
        or any(item.challenger_score - item.champion_score < _PROMOTION_MARGIN for item in receipts)
        or any(
            item.error_count + item.data_eligibility_failures + item.order_mismatches + item.research_task_losses > 0
            for item in receipts
        )
    )


def health_reasons(receipt: KrLoopHealthReceipt) -> tuple[str, ...]:
    reasons: list[str] = []
    if receipt.error_rate > _MAX_ERROR_RATE:
        reasons.append("error_rate_threshold")
    if receipt.data_eligibility_failures:
        reasons.append("data_eligibility_failure")
    if receipt.order_mismatches:
        reasons.append("order_mismatch")
    if receipt.research_task_losses:
        reasons.append("research_task_loss")
    return tuple(sorted(reasons))


__all__ = ("health_reasons", "promotion_blocked", "validation_reasons")
