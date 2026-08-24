from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from enum import StrEnum, unique
from typing import Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_intraday_market_gate import KrMarketConstraintSnapshot
from trading_agent.kr_theme_day_setup import KrCompletedMinuteBar
from trading_agent.signal_contract_models import OpportunitySnapshot

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


@unique
class KrDayDecisionStatus(StrEnum):
    INVESTIGATING = "INVESTIGATING"
    ARMED = "ARMED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    EXPIRED = "EXPIRED"


@unique
class KrDayDecisionReasonCode(StrEnum):
    THEME_BREADTH_MISSING = "THEME_BREADTH_MISSING"
    CATALYST_SOURCE_MISSING = "CATALYST_SOURCE_MISSING"
    VOLUME_CONFIRMATION_MISSING = "VOLUME_CONFIRMATION_MISSING"
    FLOW_CONFIRMATION_MISSING = "FLOW_CONFIRMATION_MISSING"
    PRICE_SETUP_INCOMPLETE = "PRICE_SETUP_INCOMPLETE"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    MARKET_GATE_BLOCKED = "MARKET_GATE_BLOCKED"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    DUPLICATE_THESIS = "DUPLICATE_THESIS"
    OPPORTUNITY_EXPIRED = "OPPORTUNITY_EXPIRED"


KrDayDecisionReason = KrDayDecisionReasonCode


class InvalidKrDayDecisionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day pre-entry decision is invalid"


class InvalidKrDayCandidateAdmissionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day candidate admission input is invalid"


class KrDayDecisionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class KrDayDecisionEvidenceValue(KrDayDecisionModel):
    name: str
    value: str

    @model_validator(mode="after")
    def validate_value(self) -> Self:
        if not _canonical_text(self.name) or not _canonical_text(self.value):
            raise InvalidKrDayDecisionError
        return self


class KrDayCandidateAdmissionPolicy(KrDayDecisionModel):
    policy_id: Literal["kr-day-candidate-admission-v1"] = "kr-day-candidate-admission-v1"
    capsule_id: str = Field(pattern=_SHA256_PATTERN)
    hypothesis_version_id: str = Field(pattern=_SHA256_PATTERN)
    min_related_symbol_count: int = Field(gt=0)
    min_catalyst_count: int = Field(gt=0)
    min_publisher_count: int = Field(gt=0)
    min_opportunity_volume_ratio: Decimal = Field(gt=0)
    min_completed_bar_volume_ratio: Decimal = Field(gt=0)
    min_trading_value_krw: Decimal = Field(gt=0)
    min_completed_bar_trading_value_krw: Decimal = Field(gt=0)
    min_completed_bar_price_response: Decimal = Field(ge=0)
    max_spread_bps: Decimal = Field(gt=0)


class KrDayCandidateAdmissionRequest(KrDayDecisionModel):
    policy: KrDayCandidateAdmissionPolicy
    capsule_id: str = Field(pattern=_SHA256_PATTERN)
    hypothesis_version_id: str = Field(pattern=_SHA256_PATTERN)
    opportunity: OpportunitySnapshot
    market: KrMarketConstraintSnapshot
    bars: tuple[KrCompletedMinuteBar, ...]
    evaluated_at: dt.datetime
    active_thesis_keys: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        keys_are_canonical = self.active_thesis_keys == tuple(sorted(set(self.active_thesis_keys)))
        if (
            not _aware(self.evaluated_at)
            or not keys_are_canonical
            or any(not _sha256(value) for value in self.active_thesis_keys)
        ):
            raise InvalidKrDayCandidateAdmissionError
        return self


class KrDayCandidateAdmissionResult(KrDayDecisionModel):
    admitted: bool
    status: KrDayDecisionStatus
    reason_codes: tuple[KrDayDecisionReasonCode, ...]
    thesis_key: str = Field(pattern=_SHA256_PATTERN)
    observed_evidence: tuple[KrDayDecisionEvidenceValue, ...]
    source_evidence_refs: tuple[str, ...]

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        names = tuple(item.name for item in self.observed_evidence)
        reasons = tuple(item.value for item in self.reason_codes)
        canonical = names == tuple(sorted(set(names))) and reasons == tuple(sorted(set(reasons)))
        canonical = canonical and self.source_evidence_refs == tuple(sorted(set(self.source_evidence_refs)))
        permitted = {KrDayDecisionStatus.REJECTED, KrDayDecisionStatus.BLOCKED, KrDayDecisionStatus.EXPIRED}
        outcome = self.admitted == (self.status is KrDayDecisionStatus.INVESTIGATING and not self.reason_codes)
        if (
            not canonical
            or not self.source_evidence_refs
            or not outcome
            or (not self.admitted and self.status not in permitted)
        ):
            raise InvalidKrDayCandidateAdmissionError
        return self


class KrDayConditionalPlan(KrDayDecisionModel):
    trigger_rule: str
    trigger_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    target_prices: tuple[Decimal, ...] = Field(min_length=1)
    invalidation_rule: str
    valid_until: dt.datetime
    rationale: str
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    capsule_id: str = Field(pattern=_SHA256_PATTERN)
    hypothesis_version_id: str = Field(pattern=_SHA256_PATTERN)
    paper_only: Literal[True] = True

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        text = (self.trigger_rule, self.invalidation_rule, self.rationale)
        if (
            not all(_canonical_text(value) for value in text)
            or not _aware(self.valid_until)
            or not _canonical_items(self.evidence_refs)
            or any(price <= 0 for price in self.target_prices)
            or self.target_prices != tuple(sorted(set(self.target_prices)))
            or not self.stop_price < self.trigger_price < self.target_prices[0]
        ):
            raise InvalidKrDayDecisionError
        return self


class KrDayDecisionEventPayload(KrDayDecisionModel):
    schema_version: Literal[1] = 1
    capsule_id: str = Field(pattern=_SHA256_PATTERN)
    hypothesis_version_id: str = Field(pattern=_SHA256_PATTERN)
    opportunity_id: str = Field(pattern=_SHA256_PATTERN)
    session_date: dt.date
    symbol: str
    completed_bar_at: dt.datetime
    observed_at: dt.datetime
    valid_until: dt.datetime
    status: KrDayDecisionStatus
    reason_codes: tuple[KrDayDecisionReasonCode, ...] = Field(min_length=1)
    conditional_plan: KrDayConditionalPlan | None
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    observed_evidence: tuple[KrDayDecisionEvidenceValue, ...] = ()
    previous_event_id: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    research_only: Literal[True] = True
    paper_only: Literal[True] = True
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        times_are_valid = (
            _aware(self.completed_bar_at)
            and _aware(self.observed_at)
            and _aware(self.valid_until)
            and self.completed_bar_at.date() == self.session_date
            and self.completed_bar_at <= self.observed_at
            and self.completed_bar_at <= self.valid_until
        )
        reasons = tuple(reason.value for reason in self.reason_codes)
        if (
            not _canonical_text(self.symbol)
            or not times_are_valid
            or reasons != tuple(sorted(set(reasons)))
            or not _canonical_items(self.evidence_refs)
            or tuple(item.name for item in self.observed_evidence)
            != tuple(sorted({item.name for item in self.observed_evidence}))
        ):
            raise InvalidKrDayDecisionError
        _require_status_shape(self)
        return self


class KrDayDecisionEvent(KrDayDecisionEventPayload):
    event_id: str = Field(pattern=_SHA256_PATTERN)

    @classmethod
    def canonical_id_for(cls, payload: KrDayDecisionEventPayload) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        payload = KrDayDecisionEventPayload.model_validate(
            self.model_dump(mode="python", exclude={"event_id"})
        )
        if self.event_id != self.canonical_id_for(payload) or self.previous_event_id == self.event_id:
            raise InvalidKrDayDecisionError
        return self


def _require_status_shape(event: KrDayDecisionEventPayload) -> None:
    match event.status:
        case KrDayDecisionStatus.ARMED:
            plan = event.conditional_plan
            valid = (
                plan is not None
                and plan.capsule_id == event.capsule_id
                and plan.hypothesis_version_id == event.hypothesis_version_id
                and plan.valid_until == event.valid_until
                and plan.evidence_refs == event.evidence_refs
                and event.observed_at < event.valid_until
            )
        case KrDayDecisionStatus.EXPIRED:
            valid = event.conditional_plan is None and event.valid_until <= event.observed_at
        case (
            KrDayDecisionStatus.INVESTIGATING
            | KrDayDecisionStatus.REJECTED
            | KrDayDecisionStatus.BLOCKED
        ):
            valid = event.conditional_plan is None and event.observed_at < event.valid_until
        case unreachable:
            assert_never(unreachable)
    if not valid:
        raise InvalidKrDayDecisionError


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip()


def _canonical_items(values: tuple[str, ...]) -> bool:
    return all(_canonical_text(value) for value in values) and values == tuple(sorted(set(values)))


def _sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


__all__ = (
    "InvalidKrDayCandidateAdmissionError",
    "InvalidKrDayDecisionError",
    "KrDayCandidateAdmissionPolicy",
    "KrDayCandidateAdmissionRequest",
    "KrDayCandidateAdmissionResult",
    "KrDayConditionalPlan",
    "KrDayDecisionEvent",
    "KrDayDecisionEventPayload",
    "KrDayDecisionEvidenceValue",
    "KrDayDecisionReason",
    "KrDayDecisionReasonCode",
    "KrDayDecisionStatus",
)
