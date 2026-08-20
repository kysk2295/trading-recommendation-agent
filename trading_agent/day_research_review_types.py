from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

from pydantic import BaseModel, ConfigDict

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json


@dataclass(frozen=True, slots=True)
class InvalidDayResearchReviewError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class DayPromotionStatus(StrEnum):
    REJECTED = "rejected"
    INSUFFICIENT = "insufficient"
    SHADOW_CANDIDATE = "shadow_candidate"
    PAPER_TRIAL_CANDIDATE = "paper_trial_candidate"
    PAPER_CHAMPION_CANDIDATE = "paper_champion_candidate"


class DayExecutionAuthorityClass(StrEnum):
    PAPER_TRIAL_APPROVED = "paper_trial_approved"
    PAPER_CHAMPION = "paper_champion"


class DayExecutionEligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    SUSPENDED = "suspended"
    EXPIRED = "expired"


class DayReviewEvidenceKind(StrEnum):
    HISTORICAL = "historical"
    HOLDOUT = "holdout"
    FORWARD = "forward"
    COST = "cost"
    DATA_QUALITY = "data_quality"


class DayReviewModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


def day_review_content_id(payload: DayReviewModel) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()


def required_day_execution_authority_class(
    status: DayPromotionStatus,
) -> DayExecutionAuthorityClass | None:
    match status:
        case DayPromotionStatus.PAPER_TRIAL_CANDIDATE:
            return DayExecutionAuthorityClass.PAPER_TRIAL_APPROVED
        case DayPromotionStatus.PAPER_CHAMPION_CANDIDATE:
            return DayExecutionAuthorityClass.PAPER_CHAMPION
        case DayPromotionStatus.REJECTED | DayPromotionStatus.INSUFFICIENT | DayPromotionStatus.SHADOW_CANDIDATE:
            return None
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "DayExecutionAuthorityClass",
    "DayExecutionEligibilityStatus",
    "DayPromotionStatus",
    "DayReviewEvidenceKind",
    "DayReviewModel",
    "InvalidDayResearchReviewError",
    "day_review_content_id",
    "required_day_execution_authority_class",
)
