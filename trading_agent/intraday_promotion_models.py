from __future__ import annotations

import datetime as dt
import hashlib
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import StrategyLifecycleState
from trading_agent.us_equity_calendar import NEW_YORK

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class PromotionAssessmentStatus(StrEnum):
    BLOCKED = "blocked"
    ELIGIBLE = "eligible"
    MANUAL_APPROVAL_PENDING = "manual_approval_pending"


class PromotionAssessmentContent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_version: str
    decision_session_date: dt.date
    assessed_at: dt.datetime
    target_state: StrategyLifecycleState
    evidence_keys: tuple[str, ...]
    status: PromotionAssessmentStatus
    blockers: tuple[str, ...]

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        champion = self.target_state in {
            StrategyLifecycleState.SHADOW_CHAMPION,
            StrategyLifecycleState.PAPER_CHAMPION,
        }
        status_valid = (
            (
                self.status is PromotionAssessmentStatus.ELIGIBLE
                and not self.blockers
            )
            or (
                self.status is PromotionAssessmentStatus.MANUAL_APPROVAL_PENDING
                and self.blockers == ("manual_approval_required",)
            )
            or (
                self.status is PromotionAssessmentStatus.BLOCKED
                and bool(self.blockers)
                and self.blockers != ("manual_approval_required",)
            )
        )
        if (
            _IDENTIFIER.fullmatch(self.strategy_version) is None
            or not _aware(self.assessed_at)
            or self.assessed_at.astimezone(NEW_YORK).date() != self.decision_session_date
            or not champion
            or self.evidence_keys != tuple(sorted(set(self.evidence_keys)))
            or len(self.evidence_keys) != 6
            or any(_HEX64.fullmatch(value) is None for value in self.evidence_keys)
            or self.blockers != tuple(sorted(set(self.blockers)))
            or not status_valid
        ):
            raise ValueError("invalid intraday promotion assessment content")
        return self


class IntradayPromotionAssessment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    assessment_id: str
    content: PromotionAssessmentContent

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        if self.assessment_id != assessment_id(self.content):
            raise ValueError("invalid intraday promotion assessment identity")
        return self


class PromotionApprovalContent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    assessment_id: str
    strategy_version: str
    decision_session_date: dt.date
    target_state: StrategyLifecycleState
    approver: str
    approved_at: dt.datetime

    @model_validator(mode="after")
    def validate_content(self) -> Self:
        if (
            _HEX64.fullmatch(self.assessment_id) is None
            or _IDENTIFIER.fullmatch(self.strategy_version) is None
            or _IDENTIFIER.fullmatch(self.approver) is None
            or not _aware(self.approved_at)
            or self.approved_at.astimezone(NEW_YORK).date() != self.decision_session_date
            or self.target_state
            not in {
                StrategyLifecycleState.SHADOW_CHAMPION,
                StrategyLifecycleState.PAPER_CHAMPION,
            }
        ):
            raise ValueError("invalid intraday promotion approval content")
        return self


class IntradayPromotionApproval(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    approval_id: str
    content: PromotionApprovalContent

    @model_validator(mode="after")
    def validate_approval(self) -> Self:
        if self.approval_id != approval_id(self.content):
            raise ValueError("invalid intraday promotion approval identity")
        return self


def assessment_id(content: PromotionAssessmentContent) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(content).encode()).hexdigest()


def approval_id(content: PromotionApprovalContent) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(content).encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "IntradayPromotionApproval",
    "IntradayPromotionAssessment",
    "PromotionApprovalContent",
    "PromotionAssessmentContent",
    "PromotionAssessmentStatus",
    "approval_id",
    "assessment_id",
)
