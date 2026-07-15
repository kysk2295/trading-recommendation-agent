from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.adaptive_evaluation_models import AdaptiveAction
from trading_agent.lane_policy_models import LaneId

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class LaneReviewerAction(StrEnum):
    CONTINUE_COLLECTION = "continue_collection"
    STOP_RECOMMENDED = "stop_recommended"
    DIAGNOSIS_REQUIRED = "diagnosis_required"
    COMPARISON_READY = "comparison_ready"
    PROMOTION_REVIEW_BLOCKED = "promotion_review_blocked"


class LaneReviewEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    lane_id: LaneId
    session_date: dt.date
    snapshot_key: str
    experiment_scope_key: str
    daily_record_id: str
    daily_record_sha256: str
    adaptive_evaluation_sha256: str
    strategy_version: str
    evaluator_version: str
    reviewer_version: str
    adaptive_action: AdaptiveAction
    reviewer_action: LaneReviewerAction
    reasons: tuple[str, ...]
    blockers: tuple[str, ...]
    reviewed_at: dt.datetime
    automatic_state_change_allowed: Literal[False]
    order_authority_change_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        hashes = (
            self.snapshot_key,
            self.experiment_scope_key,
            self.daily_record_id,
            self.daily_record_sha256,
            self.adaptive_evaluation_sha256,
        )
        versions = (
            self.strategy_version,
            self.evaluator_version,
            self.reviewer_version,
        )
        if (
            not all(_HEX64.fullmatch(value) for value in hashes)
            or not all(_IDENTIFIER.fullmatch(value) for value in versions)
            or not _aware(self.reviewed_at)
            or not _canonical_texts(self.reasons)
            or not _canonical_texts(self.blockers)
        ):
            raise ValueError("invalid immutable lane review event")
        return self


def _canonical_texts(values: tuple[str, ...]) -> bool:
    return values == tuple(sorted(set(values))) and all(value and value == value.strip() for value in values)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
