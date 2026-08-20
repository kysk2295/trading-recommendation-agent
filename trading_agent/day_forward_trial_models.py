from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Mapping
from typing import Literal, Self, assert_never

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.day_forward_trial_identity import (
    CanonicalValue,
    DayForwardExitReason,
    DayForwardTrialEventKind,
    ForwardExecutionLane,
    canonical_forward_trial_sha256,
    market_clock,
)
from trading_agent.research_identity_models import MarketId

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_REF = re.compile(r"^artifact://safe/[0-9a-f]{64}$")
_SESSION_ID = re.compile(r"^(XNYS|XKRX)-[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_CALENDAR_ID = re.compile(r"^calendar://official/(XNYS|XKRX)/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REASON = re.compile(r"^[a-z0-9][a-z0-9_]{0,127}$")


class InvalidDayForwardTrialModelError(ValueError):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


class ForwardTrialModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
    )


class DayForwardBarOutcomeRequest(ForwardTrialModel):
    low: float
    high: float
    stop: float
    target: float

    @model_validator(mode="after")
    def valid_prices(self) -> Self:
        values = (self.low, self.high, self.stop, self.target)
        if not all(math.isfinite(value) for value in values) or self.low > self.high or self.stop >= self.target:
            raise InvalidDayForwardTrialModelError("forward_bar_outcome_invalid")
        return self


class DayForwardTrial(ForwardTrialModel):
    schema_version: Literal[1] = 1
    trial_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    hypothesis_version_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    market_id: MarketId
    execution_lane: ForwardExecutionLane
    session_id: str = Field(pattern=r"^(XNYS|XKRX)-[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    session_date: dt.date
    calendar_snapshot_id: str
    cost_model_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_refs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preregistered_at: AwareDatetime
    registration_completed_bar_at: AwareDatetime
    first_eligible_completed_bar_at: AwareDatetime
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, CanonicalValue]) -> str:
        canonical = dict(payload)
        canonical.pop("trial_id", None)
        return canonical_forward_trial_sha256(canonical)

    @model_validator(mode="after")
    def valid_trial(self) -> Self:
        exchange, timezone = market_clock(self.market_id)
        if (
            _SESSION_ID.fullmatch(self.session_id) is None
            or self.session_id != f"{exchange}-{self.session_date.isoformat()}"
            or _CALENDAR_ID.fullmatch(self.calendar_snapshot_id) is None
            or not self.calendar_snapshot_id.startswith(f"calendar://official/{exchange}/")
            or self.first_eligible_completed_bar_at.astimezone(timezone).date() != self.session_date
        ):
            raise InvalidDayForwardTrialModelError("forward_trial_session_invalid")
        if not (
            self.registration_completed_bar_at
            <= self.preregistered_at
            < self.first_eligible_completed_bar_at
            and self.registration_completed_bar_at < self.first_eligible_completed_bar_at
        ):
            raise InvalidDayForwardTrialModelError("forward_trial_not_future")
        if self.trial_id != self.canonical_id_for(self.model_dump(mode="python")):
            raise InvalidDayForwardTrialModelError("forward_trial_identity_invalid")
        return self


class DayForwardOutcomeRef(ForwardTrialModel):
    schema_version: Literal[1] = 1
    outcome_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_ref: str
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recorded_at: AwareDatetime
    profitability_claim: Literal[False] = False

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, CanonicalValue]) -> str:
        canonical = dict(payload)
        canonical.pop("outcome_id", None)
        return canonical_forward_trial_sha256(canonical)

    @model_validator(mode="after")
    def valid_outcome(self) -> Self:
        if (
            _ARTIFACT_REF.fullmatch(self.artifact_ref) is None
            or self.artifact_ref != f"artifact://safe/{self.artifact_sha256}"
            or self.outcome_id != self.canonical_id_for(self.model_dump(mode="python"))
        ):
            raise InvalidDayForwardTrialModelError("forward_outcome_ref_invalid")
        return self


class DayForwardTrialEvent(ForwardTrialModel):
    schema_version: Literal[1] = 1
    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    trial_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    market_id: MarketId
    session_id: str
    session_date: dt.date
    sequence: int = Field(ge=1)
    previous_event_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    event_kind: DayForwardTrialEventKind
    completed_bar_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_bar_sequence: int = Field(ge=1)
    completed_bar_at: AwareDatetime
    event_at: AwareDatetime
    exit_reason: DayForwardExitReason | None
    outcome_ref: DayForwardOutcomeRef | None
    reason_codes: tuple[str, ...]
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, CanonicalValue]) -> str:
        canonical = dict(payload)
        canonical.pop("event_id", None)
        return canonical_forward_trial_sha256(canonical)

    @model_validator(mode="after")
    def valid_event(self) -> Self:
        if (
            self.event_at < self.completed_bar_at
            or (self.sequence == 1) is not (self.previous_event_id is None)
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
            or any(_REASON.fullmatch(reason) is None for reason in self.reason_codes)
        ):
            raise InvalidDayForwardTrialModelError("forward_trial_event_invalid")
        match self.event_kind:
            case (
                DayForwardTrialEventKind.SIGNAL
                | DayForwardTrialEventKind.ENTRY
                | DayForwardTrialEventKind.OBSERVED
                | DayForwardTrialEventKind.NO_SIGNAL
            ):
                valid_payload = self.exit_reason is None and self.outcome_ref is None and not self.reason_codes
            case DayForwardTrialEventKind.EXIT:
                valid_payload = self.exit_reason is not None and self.outcome_ref is not None and not self.reason_codes
            case DayForwardTrialEventKind.BLOCKED | DayForwardTrialEventKind.FAILED:
                valid_payload = self.exit_reason is None and self.outcome_ref is None and bool(self.reason_codes)
            case DayForwardTrialEventKind.CENSORED:
                valid_payload = self.exit_reason is None and bool(self.reason_codes)
            case unreachable:
                assert_never(unreachable)
        if not valid_payload:
            raise InvalidDayForwardTrialModelError("forward_trial_event_payload_invalid")
        if self.event_id != self.canonical_id_for(self.model_dump(mode="python")):
            raise InvalidDayForwardTrialModelError("forward_trial_event_identity_invalid")
        return self


def resolve_day_forward_bar_outcome(
    request: DayForwardBarOutcomeRequest,
) -> DayForwardExitReason | None:
    if request.low <= request.stop:
        return DayForwardExitReason.STOP
    if request.high >= request.target:
        return DayForwardExitReason.TARGET
    return None


__all__ = (
    "DayForwardBarOutcomeRequest",
    "DayForwardExitReason",
    "DayForwardOutcomeRef",
    "DayForwardTrial",
    "DayForwardTrialEvent",
    "DayForwardTrialEventKind",
    "ForwardExecutionLane",
    "InvalidDayForwardTrialModelError",
    "resolve_day_forward_bar_outcome",
)
