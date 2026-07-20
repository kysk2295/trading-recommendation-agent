from __future__ import annotations

import datetime as dt
import hashlib
import re
from enum import StrEnum
from typing import Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")


class InvalidUsNewsCatalystTrialModelError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst trial model is invalid"


class UsNewsCatalystDailyTrialRegistrationRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    strategy_version: str
    code_version: str
    session_date: dt.date
    registered_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            not _canonical_text(self.strategy_version)
            or not _canonical_text(self.code_version)
            or not _aware(self.registered_at)
        ):
            raise InvalidUsNewsCatalystTrialModelError
        return self


class UsNewsCatalystCohortStatus(StrEnum):
    INSUFFICIENT_CONTROL = "insufficient_control"
    READY = "ready"


class UsNewsCatalystCohortPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    trial_id: str
    strategy_version: str
    session_date: dt.date
    projection_id: str
    evidence_bundle_id: str
    opportunity_id: str
    observed_at: dt.datetime
    treatment_symbols: tuple[str, ...] = Field(min_length=1, max_length=20)
    control_symbols: tuple[str, ...] = Field(max_length=20)
    status: UsNewsCatalystCohortStatus

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        treatment = self.treatment_symbols
        control = self.control_symbols
        if (
            not _canonical_text(self.trial_id)
            or not _canonical_text(self.strategy_version)
            or _HEX64.fullmatch(self.projection_id) is None
            or _HEX64.fullmatch(self.evidence_bundle_id) is None
            or not _canonical_text(self.opportunity_id)
            or not _aware(self.observed_at)
            or len(treatment) != len(set(treatment))
            or len(control) != len(set(control))
            or not all(_SYMBOL.fullmatch(symbol) for symbol in (*treatment, *control))
            or set(treatment).intersection(control)
        ):
            raise InvalidUsNewsCatalystTrialModelError
        match self.status:
            case UsNewsCatalystCohortStatus.READY:
                if not control or len(control) != len(treatment):
                    raise InvalidUsNewsCatalystTrialModelError
            case UsNewsCatalystCohortStatus.INSUFFICIENT_CONTROL:
                if len(control) >= len(treatment):
                    raise InvalidUsNewsCatalystTrialModelError
            case unreachable:
                assert_never(unreachable)
        return self


class UsNewsCatalystCohortArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str
    payload: UsNewsCatalystCohortPayload

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        if self.artifact_id != _payload_id(self.payload):
            raise InvalidUsNewsCatalystTrialModelError
        return self


def cohort_artifact(payload: UsNewsCatalystCohortPayload) -> UsNewsCatalystCohortArtifact:
    return UsNewsCatalystCohortArtifact(artifact_id=_payload_id(payload), payload=payload)


def _payload_id(payload: BaseModel) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip() and not any(char in value for char in "\r\n\t")


def _canonical_set(values: tuple[str, ...]) -> bool:
    return values == tuple(sorted(set(values))) and all(_canonical_text(value) for value in values)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidUsNewsCatalystTrialModelError",
    "UsNewsCatalystCohortArtifact",
    "UsNewsCatalystCohortPayload",
    "UsNewsCatalystCohortStatus",
    "UsNewsCatalystDailyTrialRegistrationRequest",
    "cohort_artifact",
)
