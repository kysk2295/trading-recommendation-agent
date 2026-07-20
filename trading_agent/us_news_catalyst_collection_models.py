from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_INSTRUMENT = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_MAX_COLLECTION_DELAY = dt.timedelta(minutes=2)


class InvalidUsNewsCatalystCollectionModelError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst cohort collection artifact is invalid"


class UsNewsCatalystCollectionProfileBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    instrument_id: str
    profile_evidence_sha256: str

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if (
            _SYMBOL.fullmatch(self.symbol) is None
            or _INSTRUMENT.fullmatch(self.instrument_id) is None
            or _HEX64.fullmatch(self.profile_evidence_sha256) is None
        ):
            raise InvalidUsNewsCatalystCollectionModelError
        return self


class UsNewsCatalystCollectionPlanContent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    cohort_artifact_id: str
    trial_id: str
    session_date: dt.date
    cohort_observed_at: dt.datetime
    evaluated_at: dt.datetime
    completed_minute: int = Field(ge=1, le=390)
    security_master_snapshot_id: str
    bindings: tuple[UsNewsCatalystCollectionProfileBinding, ...] = Field(
        min_length=2,
        max_length=40,
    )

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        symbols = tuple(item.symbol for item in self.bindings)
        instruments = tuple(item.instrument_id for item in self.bindings)
        bounds = regular_session_bounds(self.session_date)
        target = self.cohort_observed_at + dt.timedelta(minutes=30)
        if (
            _HEX64.fullmatch(self.cohort_artifact_id) is None
            or not _canonical_text(self.trial_id)
            or not _aware(self.cohort_observed_at)
            or not _aware(self.evaluated_at)
            or self.cohort_observed_at.astimezone(NEW_YORK).date() != self.session_date
            or bounds is None
            or not bounds[0] < self.evaluated_at < bounds[1]
            or not target < self.evaluated_at <= target + _MAX_COLLECTION_DELAY
            or self.completed_minute != _completed_minute(bounds[0], self.evaluated_at)
            or _HEX64.fullmatch(self.security_master_snapshot_id) is None
            or symbols != tuple(sorted(set(symbols)))
            or len(instruments) != len(set(instruments))
        ):
            raise InvalidUsNewsCatalystCollectionModelError
        return self


class UsNewsCatalystCollectionPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    plan_id: str
    content: UsNewsCatalystCollectionPlanContent

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        if _HEX64.fullmatch(self.plan_id) is None or self.plan_id != _model_id(self.content):
            raise InvalidUsNewsCatalystCollectionModelError
        return self


class UsNewsCatalystCollectedFeatureRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    artifact_id: str

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        if _SYMBOL.fullmatch(self.symbol) is None or _HEX64.fullmatch(self.artifact_id) is None:
            raise InvalidUsNewsCatalystCollectionModelError
        return self


class UsNewsCatalystCollectionReceiptContent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    plan_id: str
    cohort_artifact_id: str
    evaluated_at: dt.datetime
    features: tuple[UsNewsCatalystCollectedFeatureRef, ...] = Field(
        min_length=2,
        max_length=40,
    )

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        symbols = tuple(item.symbol for item in self.features)
        if (
            _HEX64.fullmatch(self.plan_id) is None
            or _HEX64.fullmatch(self.cohort_artifact_id) is None
            or not _aware(self.evaluated_at)
            or symbols != tuple(sorted(set(symbols)))
        ):
            raise InvalidUsNewsCatalystCollectionModelError
        return self


class UsNewsCatalystCollectionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    receipt_id: str
    content: UsNewsCatalystCollectionReceiptContent

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if _HEX64.fullmatch(self.receipt_id) is None or self.receipt_id != _model_id(self.content):
            raise InvalidUsNewsCatalystCollectionModelError
        return self


def create_us_news_catalyst_collection_plan(
    content: UsNewsCatalystCollectionPlanContent,
) -> UsNewsCatalystCollectionPlan:
    return UsNewsCatalystCollectionPlan(
        plan_id=_model_id(content),
        content=content,
    )


def create_us_news_catalyst_collection_receipt(
    plan: UsNewsCatalystCollectionPlan,
    features: tuple[UsNewsCatalystCollectedFeatureRef, ...],
) -> UsNewsCatalystCollectionReceipt:
    content = UsNewsCatalystCollectionReceiptContent(
        plan_id=plan.plan_id,
        cohort_artifact_id=plan.content.cohort_artifact_id,
        evaluated_at=plan.content.evaluated_at,
        features=features,
    )
    return UsNewsCatalystCollectionReceipt(
        receipt_id=_model_id(content),
        content=content,
    )


def _model_id(model: BaseModel) -> str:
    payload = model.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _completed_minute(opened_at: dt.datetime, evaluated_at: dt.datetime) -> int:
    boundary = evaluated_at.replace(second=0, microsecond=0)
    return int((boundary - opened_at) / dt.timedelta(minutes=1))


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip() and not any(char in value for char in "\r\n\t")


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidUsNewsCatalystCollectionModelError",
    "UsNewsCatalystCollectedFeatureRef",
    "UsNewsCatalystCollectionPlan",
    "UsNewsCatalystCollectionPlanContent",
    "UsNewsCatalystCollectionProfileBinding",
    "UsNewsCatalystCollectionReceipt",
    "UsNewsCatalystCollectionReceiptContent",
    "create_us_news_catalyst_collection_plan",
    "create_us_news_catalyst_collection_receipt",
)
