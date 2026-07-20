from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.us_equity_calendar import NEW_YORK

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_INSTRUMENT = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


class InvalidUsNewsCatalystFeatureModelError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst feature artifact is invalid"


class UsNewsCatalystFeaturePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    symbol: str
    instrument_id: str
    session_date: dt.date
    observed_at: dt.datetime
    source_end_at: dt.datetime
    research_input_identity_sha256: str
    volume_profile_evidence_sha256: str
    indicator_semantic_version: str
    close: Decimal
    vwap: Decimal
    rvol: Decimal
    breakout_close_above_prior_high: bool

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        values = (self.close, self.vwap, self.rvol)
        if (
            _SYMBOL.fullmatch(self.symbol) is None
            or _INSTRUMENT.fullmatch(self.instrument_id) is None
            or not _aware(self.observed_at)
            or not _aware(self.source_end_at)
            or self.observed_at.astimezone(NEW_YORK).date() != self.session_date
            or self.source_end_at >= self.observed_at
            or _HEX64.fullmatch(self.research_input_identity_sha256) is None
            or _HEX64.fullmatch(self.volume_profile_evidence_sha256) is None
            or not _canonical_text(self.indicator_semantic_version)
            or any(not _positive(value) for value in values)
        ):
            raise InvalidUsNewsCatalystFeatureModelError
        return self


class UsNewsCatalystFeatureArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str
    payload: UsNewsCatalystFeaturePayload

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        if _HEX64.fullmatch(self.artifact_id) is None or self.artifact_id != feature_payload_id(
            self.payload
        ):
            raise InvalidUsNewsCatalystFeatureModelError
        return self


def feature_artifact(
    payload: UsNewsCatalystFeaturePayload,
) -> UsNewsCatalystFeatureArtifact:
    return UsNewsCatalystFeatureArtifact(
        artifact_id=feature_payload_id(payload),
        payload=payload,
    )


def feature_payload_id(payload: UsNewsCatalystFeaturePayload) -> str:
    return hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()


def _positive(value: Decimal) -> bool:
    return type(value) is Decimal and value.is_finite() and value > 0


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip() and not any(char in value for char in "\r\n\t")


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidUsNewsCatalystFeatureModelError",
    "UsNewsCatalystFeatureArtifact",
    "UsNewsCatalystFeaturePayload",
    "feature_artifact",
)
