from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, assert_never, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.kr_autonomous_trade_models import KrAutonomousTradeOutcome
from trading_agent.kr_social_signal_models import KrSocialVerificationState

_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
_SHA = r"^[a-f0-9]{64}$"


class KrOutcomeExecutionState(StrEnum):
    NO_TRADE = "no_trade"
    REJECTED = "rejected"
    RECOMMENDATION_PENDING = "recommendation_pending"
    VIRTUAL_ARMED = "virtual_armed"
    VIRTUAL_ACTIVE = "virtual_active"
    VIRTUAL_STOPPED = "virtual_stopped"
    VIRTUAL_TARGETED = "virtual_targeted"
    VIRTUAL_EXPIRED = "virtual_expired"
    VIRTUAL_CENSORED = "virtual_censored"


class KrOutcomeHorizon(StrEnum):
    MINUTES_5 = "5m"
    MINUTES_15 = "15m"
    MINUTES_30 = "30m"
    SESSION_CLOSE = "close"


class KrOutcomeMarketEvidenceState(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    MISSING_SPREAD = "missing_spread"
    NOT_RECORDED = "not_recorded"


class KrOutcomeSessionPhase(StrEnum):
    OPENING = "opening"
    CONTINUOUS = "continuous"
    CLOSING = "closing"
    OUTSIDE_SESSION = "outside_session"


class KrLoopFailureCode(StrEnum):
    CRITIC_CHRONOLOGY = "critic_chronology"
    CRITIC_CLUSTER_COUNT = "critic_cluster_count"
    MARKET_DATA = "market_data"
    VIRTUAL_CENSORED = "virtual_censored"
    VIRTUAL_STOP = "virtual_stop"


class InvalidKrAutonomousOutcomeError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR autonomous outcome memory is invalid"


class KrOutcomePriceLevels(BaseModel):
    model_config = _STRICT

    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, Decimal]
    quantity: int = Field(gt=0)


class KrOutcomeHorizonObservation(BaseModel):
    model_config = _STRICT

    horizon: KrOutcomeHorizon
    observed_at: AwareDatetime
    close: Decimal
    return_bps: Decimal
    evidence_ref: str = Field(min_length=1, max_length=512)


class KrAutonomousOutcomeMemory(BaseModel):
    model_config = _STRICT

    schema_version: Literal[1] = 1
    outcome_id: str = Field(pattern=_SHA)
    task_id: str = Field(pattern=_SHA)
    trade_event_id: str = Field(pattern=_SHA)
    position_event_id: str | None = Field(default=None, pattern=_SHA)
    trade_outcome: KrAutonomousTradeOutcome
    execution_state: KrOutcomeExecutionState
    symbol: str
    theme: str = Field(min_length=1, max_length=160)
    verification_state: KrSocialVerificationState
    independent_source_count: int = Field(ge=1, le=64)
    independent_source_cluster_ids: tuple[str, ...] = Field(max_length=16)
    decision_reason_codes: tuple[str, ...] = Field(max_length=16)
    market_evidence_state: KrOutcomeMarketEvidenceState
    session_phase: KrOutcomeSessionPhase
    price_levels: KrOutcomePriceLevels | None
    horizons: tuple[KrOutcomeHorizonObservation, ...] = Field(max_length=4)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=32)
    lineage_sha256: str = Field(pattern=_SHA)
    observed_at: AwareDatetime
    virtual_only: Literal[True] = True
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        order = tuple(KrOutcomeHorizon)
        horizons = tuple(item.horizon for item in self.horizons)
        if (
            self.independent_source_cluster_ids != tuple(sorted(set(self.independent_source_cluster_ids)))
            or self.decision_reason_codes != tuple(sorted(set(self.decision_reason_codes)))
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
            or len(set(horizons)) != len(horizons)
            or horizons != tuple(sorted(horizons, key=order.index))
            or self.outcome_id != kr_autonomous_outcome_id(self)
        ):
            raise InvalidKrAutonomousOutcomeError
        return self


class KrLoopEngineerEvidenceBundle(BaseModel):
    model_config = _STRICT

    schema_version: Literal[1] = 1
    bundle_id: str = Field(pattern=_SHA)
    failure_code: KrLoopFailureCode
    subject_ref: str = Field(min_length=1, max_length=160)
    source_memory_ids: tuple[str, ...] = Field(min_length=3, max_length=16)
    source_task_ids: tuple[str, ...] = Field(min_length=3, max_length=16)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=16)
    change_hypothesis: str = Field(min_length=8, max_length=500)
    created_at: AwareDatetime
    code_mutation_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_bundle(self) -> Self:
        values = (self.source_memory_ids, self.source_task_ids, self.evidence_refs)
        if any(item != tuple(sorted(set(item))) for item in values) or self.bundle_id != kr_loop_engineer_bundle_id(
            self
        ):
            raise InvalidKrAutonomousOutcomeError
        return self


def canonical_kr_autonomous_outcome_json(outcome: KrAutonomousOutcomeMemory) -> str:
    return _canonical(outcome)


def canonical_kr_loop_engineer_bundle_json(bundle: KrLoopEngineerEvidenceBundle) -> str:
    return _canonical(bundle)


def kr_autonomous_outcome_id(outcome: KrAutonomousOutcomeMemory) -> str:
    return _content_id(outcome, "outcome_id")


def kr_loop_engineer_bundle_id(bundle: KrLoopEngineerEvidenceBundle) -> str:
    return _content_id(bundle, "bundle_id")


def _canonical(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _content_id(model: BaseModel, identity_field: str) -> str:
    payload = json.dumps(
        model.model_dump(mode="json", exclude={identity_field}),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def execution_state_label(state: KrOutcomeExecutionState) -> str:
    match state:
        case KrOutcomeExecutionState.NO_TRADE:
            return "관망"
        case KrOutcomeExecutionState.REJECTED:
            return "기각"
        case KrOutcomeExecutionState.RECOMMENDATION_PENDING:
            return "가상 진입 대기"
        case KrOutcomeExecutionState.VIRTUAL_ARMED:
            return "가상 대기"
        case KrOutcomeExecutionState.VIRTUAL_ACTIVE:
            return "가상 보유"
        case KrOutcomeExecutionState.VIRTUAL_STOPPED:
            return "가상 손절"
        case KrOutcomeExecutionState.VIRTUAL_TARGETED:
            return "가상 목표"
        case KrOutcomeExecutionState.VIRTUAL_EXPIRED:
            return "가상 만료"
        case KrOutcomeExecutionState.VIRTUAL_CENSORED:
            return "가상 판정 보류"
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "InvalidKrAutonomousOutcomeError",
    "KrAutonomousOutcomeMemory",
    "KrLoopEngineerEvidenceBundle",
    "KrLoopFailureCode",
    "KrOutcomeExecutionState",
    "KrOutcomeHorizon",
    "KrOutcomeHorizonObservation",
    "KrOutcomeMarketEvidenceState",
    "KrOutcomePriceLevels",
    "KrOutcomeSessionPhase",
    "canonical_kr_autonomous_outcome_json",
    "canonical_kr_loop_engineer_bundle_json",
    "execution_state_label",
    "kr_autonomous_outcome_id",
    "kr_loop_engineer_bundle_id",
)
