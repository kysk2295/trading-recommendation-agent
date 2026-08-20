from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Self, assert_never

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_learning_report_models import MarketCloseReport
from trading_agent.day_research_review_models import ReviewFeedbackSummary
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import aware

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_OFFICIAL_CALENDAR = re.compile(r"^calendar://official/(XNYS|XKRX)/[A-Za-z0-9_.:-]{1,160}$")
_POLICY_VERSION = "day-exploration-policy-v1"


@dataclass(frozen=True, slots=True)
class InvalidDayLearningPolicyError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class ExplorationPolicyAction(StrEnum):
    KEEP = "keep"
    ROTATE_EXPLORATION = "rotate_exploration"
    SUSPEND_SHADOW = "suspend_shadow"
    NO_TRADE = "no_trade"


class DayLearningPolicyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class OfficialNextSessionCalendarSnapshot(DayLearningPolicyModel):
    calendar_snapshot_id: str
    market_id: MarketId
    report_session_date: dt.date
    effective_session_date: dt.date
    observed_at: dt.datetime

    @model_validator(mode="after")
    def validate_calendar(self) -> Self:
        expected_exchange = "XNYS" if self.market_id is MarketId.US_EQUITIES else "XKRX"
        match = _OFFICIAL_CALENDAR.fullmatch(self.calendar_snapshot_id)
        if (
            match is None
            or match.group(1) != expected_exchange
            or self.effective_session_date <= self.report_session_date
            or not aware(self.observed_at)
        ):
            raise InvalidDayLearningPolicyError("day_learning_calendar_invalid")
        return self


class ExplorationPolicyRequest(DayLearningPolicyModel):
    latest_final_report: MarketCloseReport
    feedback: tuple[ReviewFeedbackSummary, ...]
    calendar: OfficialNextSessionCalendarSnapshot
    action: ExplorationPolicyAction
    effective_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        report = self.latest_final_report.payload
        candidates = set(report.next_session.active_capsule_ids) | set(report.next_session.queued_capsule_ids)
        feedback_capsules = tuple(item.capsule_id for item in self.feedback)
        feedback_decisions = tuple(item.decision_id for item in self.feedback)
        if (
            self.calendar.market_id is not report.market_id
            or self.calendar.report_session_date != report.session_date
            or not aware(self.effective_at)
            or self.effective_at < self.calendar.observed_at
            or any(item.market_id is not report.market_id for item in self.feedback)
            or len(set(feedback_capsules)) != len(feedback_capsules)
            or len(set(feedback_decisions)) != len(feedback_decisions)
            or not set(feedback_capsules) <= candidates
        ):
            raise InvalidDayLearningPolicyError("day_learning_policy_request_invalid")
        return self


class ExplorationPolicyPayload(DayLearningPolicyModel):
    final_report_id: str = Field(pattern=_SHA256_PATTERN)
    market_id: MarketId
    action: ExplorationPolicyAction
    calendar_snapshot_id: str
    effective_session_date: dt.date
    effective_at: dt.datetime
    active_capsule_ids: tuple[str, ...] = Field(max_length=3)
    queued_capsule_ids: tuple[str, ...]
    feedback_decision_ids: tuple[str, ...]
    policy_version: str

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        expected_exchange = "XNYS" if self.market_id is MarketId.US_EQUITIES else "XKRX"
        calendar_match = _OFFICIAL_CALENDAR.fullmatch(self.calendar_snapshot_id)
        if (
            not aware(self.effective_at)
            or calendar_match is None
            or calendar_match.group(1) != expected_exchange
            or self.active_capsule_ids != tuple(sorted(set(self.active_capsule_ids)))
            or self.queued_capsule_ids != tuple(sorted(set(self.queued_capsule_ids)))
            or set(self.active_capsule_ids) & set(self.queued_capsule_ids)
            or any(
                re.fullmatch(_SHA256_PATTERN, capsule_id) is None
                for capsule_id in (*self.active_capsule_ids, *self.queued_capsule_ids)
            )
            or self.feedback_decision_ids != tuple(sorted(set(self.feedback_decision_ids)))
            or any(
                re.fullmatch(_SHA256_PATTERN, decision_id) is None
                for decision_id in self.feedback_decision_ids
            )
            or self.policy_version != _POLICY_VERSION
        ):
            raise InvalidDayLearningPolicyError("day_learning_policy_payload_invalid")
        return self


class ExplorationPolicy(DayLearningPolicyModel):
    policy_id: str = Field(pattern=_SHA256_PATTERN)
    payload: ExplorationPolicyPayload

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if self.policy_id != expected:
            raise InvalidDayLearningPolicyError("day_learning_policy_identity_invalid")
        return self


def build_exploration_policy(request: ExplorationPolicyRequest) -> ExplorationPolicy:
    checked = ExplorationPolicyRequest.model_validate(request.model_dump(mode="python"))
    next_session = checked.latest_final_report.payload.next_session
    active, queued = _select_capsules(
        checked.action,
        next_session.active_capsule_ids,
        next_session.queued_capsule_ids,
    )
    payload = ExplorationPolicyPayload(
        final_report_id=checked.latest_final_report.report_id,
        market_id=next_session.market_id,
        action=checked.action,
        calendar_snapshot_id=checked.calendar.calendar_snapshot_id,
        effective_session_date=checked.calendar.effective_session_date,
        effective_at=checked.effective_at,
        active_capsule_ids=active,
        queued_capsule_ids=queued,
        feedback_decision_ids=tuple(sorted(item.decision_id for item in checked.feedback)),
        policy_version=_POLICY_VERSION,
    )
    policy_id = hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()
    return ExplorationPolicy(policy_id=policy_id, payload=payload)


def _select_capsules(
    action: ExplorationPolicyAction,
    active: tuple[str, ...],
    queued: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    match action:
        case ExplorationPolicyAction.KEEP:
            selected = active[:3]
            waiting = tuple(sorted((*active[3:], *queued)))
        case ExplorationPolicyAction.ROTATE_EXPLORATION:
            promoted = queued[:1]
            retained = active[1:3] if promoted else active[:3]
            selected = tuple(sorted((*retained, *promoted)))
            waiting = tuple(sorted((*(active[:1] if promoted else ()), *queued[1:])))
        case ExplorationPolicyAction.SUSPEND_SHADOW | ExplorationPolicyAction.NO_TRADE:
            selected = ()
            waiting = tuple(sorted((*active, *queued)))
        case unreachable:
            assert_never(unreachable)
    return selected, waiting


__all__ = (
    "ExplorationPolicy",
    "ExplorationPolicyAction",
    "ExplorationPolicyPayload",
    "ExplorationPolicyRequest",
    "InvalidDayLearningPolicyError",
    "OfficialNextSessionCalendarSnapshot",
    "build_exploration_policy",
)
