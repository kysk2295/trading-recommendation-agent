from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import ExpectedDirection, aware

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# allow: SIZE_OK — validation and canonical identity must remain one atomic contract.

@dataclass(frozen=True, slots=True)
class InvalidDayHypothesisModelError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class DayHypothesisModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always", strict=True)

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(mode="python")
        if update is not None:
            payload.update(update)
        return self.__class__.model_validate(payload)


class MethodologyDeclaration(DayHypothesisModel):
    methodology_tags: tuple[str, ...]
    primary_evaluation_owner: str
    evaluation_cadence: str

    @model_validator(mode="after")
    def validate_methodology(self) -> Self:
        if (
            not _sorted_unique_text(self.methodology_tags)
            or not _canonical_text(self.primary_evaluation_owner)
            or not _canonical_text(self.evaluation_cadence)
        ):
            raise InvalidDayHypothesisModelError("invalid methodology declaration")
        return self


class CostModelDeclaration(DayHypothesisModel):
    model_id: str
    commission_bps: Decimal
    slippage_bps: Decimal

    @field_validator("commission_bps", "slippage_bps")
    @classmethod
    def normalize_decimal(cls, value: Decimal) -> Decimal:
        return _normalize_decimal(value)

    @model_validator(mode="after")
    def validate_cost_model(self) -> Self:
        if (
            not _canonical_text(self.model_id)
            or not _nonnegative_finite_decimal(self.commission_bps)
            or not _nonnegative_finite_decimal(self.slippage_bps)
        ):
            raise InvalidDayHypothesisModelError("invalid cost model declaration")
        return self


class FreeParameter(DayHypothesisModel):
    name: str
    values: tuple[Decimal, ...] = Field(min_length=1, max_length=32)

    @field_validator("values")
    @classmethod
    def normalize_values(cls, values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        return tuple(_normalize_decimal(value) for value in values)

    @model_validator(mode="after")
    def validate_parameter(self) -> Self:
        if not _canonical_text(self.name) or not _sorted_unique_decimals(self.values):
            raise InvalidDayHypothesisModelError("invalid free parameter")
        return self


class SearchBudget(DayHypothesisModel):
    max_parameter_combinations: int = Field(ge=1, le=10_000)
    max_attempts: int = Field(ge=1, le=10_000)
    max_cpu_seconds: int = Field(ge=1, le=86_400)


class TargetHorizon(DayHypothesisModel):
    duration: dt.timedelta = Field(gt=dt.timedelta(0), le=dt.timedelta(days=3_650))


class HypothesisFamily(DayHypothesisModel):
    family_id: str
    parent_family_id: str | None
    canonical_question: str
    economic_mechanism: str
    alternative_explanations: tuple[str, ...] = Field(min_length=1)
    counterfactual_baseline: str
    created_by: str
    created_at: dt.datetime
    source_lineage: tuple[str, ...] = Field(min_length=1)

    @field_validator("created_at")
    @classmethod
    def normalize_created_at(cls, value: dt.datetime) -> dt.datetime:
        return _normalize_datetime(value)

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, object]) -> str:
        return _canonical_identity(payload, "family_id")

    @model_validator(mode="after")
    def validate_family(self) -> Self:
        if (
            _HEX64.fullmatch(self.family_id) is None
            or (self.parent_family_id is not None and _HEX64.fullmatch(self.parent_family_id) is None)
            or not _canonical_text(self.canonical_question)
            or not _canonical_text(self.economic_mechanism)
            or not _sorted_unique_text(self.alternative_explanations)
            or not _canonical_text(self.counterfactual_baseline)
            or not _canonical_text(self.created_by)
            or not aware(self.created_at)
            or not _sorted_unique_text(self.source_lineage)
        ):
            raise InvalidDayHypothesisModelError("invalid day hypothesis family")
        if self.family_id != self.canonical_id_for(self.model_dump(mode="python")):
            raise InvalidDayHypothesisModelError("hypothesis_family_id_mismatch")
        return self


class HypothesisVersion(DayHypothesisModel):
    hypothesis_version_id: str
    family_id: str
    parent_version_id: str | None
    market_id: MarketId
    universe_snapshot_id: str
    universe_snapshot_at: dt.datetime
    source_refs: tuple[str, ...] = Field(min_length=1)
    methodology_tags: tuple[str, ...] = Field(min_length=1)
    primary_evaluation_owner: str
    evaluation_cadence: str
    predictor: str
    sampling_timestamp: dt.datetime
    target: str
    target_horizon: TargetHorizon
    expected_direction: ExpectedDirection
    entry_rule: str
    exit_rule: str
    stop_rule: str
    invalidation_rule: str
    threshold: Decimal
    cost_model: CostModelDeclaration
    free_parameters: tuple[FreeParameter, ...] = Field(max_length=12)
    search_budget: SearchBudget
    multiple_testing_family: str
    model_sha256: str
    prompt_sha256: str
    code_sha256: str
    data_manifest_sha256: str
    protocol_sha256: str
    created_at: dt.datetime
    registration_completed_bar_at: dt.datetime
    first_shadow_eligible_at: dt.datetime
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @field_validator(
        "universe_snapshot_at",
        "sampling_timestamp",
        "created_at",
        "registration_completed_bar_at",
        "first_shadow_eligible_at",
    )
    @classmethod
    def normalize_datetimes(cls, value: dt.datetime) -> dt.datetime:
        return _normalize_datetime(value)

    @field_validator("threshold")
    @classmethod
    def normalize_threshold(cls, value: Decimal) -> Decimal:
        return _normalize_decimal(value)

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, object]) -> str:
        return _canonical_identity(payload, "hypothesis_version_id")

    @field_validator("trading_authority", mode="before")
    @classmethod
    def reject_trading_authority(cls, value: bool) -> Literal[False]:
        if value is not False:
            raise InvalidDayHypothesisModelError("hypothesis_version_cannot_grant_authority")
        return False

    @field_validator("profitability_claim", mode="before")
    @classmethod
    def reject_profitability_claim(cls, value: bool) -> Literal[False]:
        if value is not False:
            raise InvalidDayHypothesisModelError("hypothesis_version_cannot_claim_profitability")
        return False

    @property
    def methodology(self) -> MethodologyDeclaration:
        return MethodologyDeclaration(
            methodology_tags=self.methodology_tags,
            primary_evaluation_owner=self.primary_evaluation_owner,
            evaluation_cadence=self.evaluation_cadence,
        )

    @model_validator(mode="after")
    def validate_version(self) -> Self:
        times = (
            self.universe_snapshot_at,
            self.sampling_timestamp,
            self.created_at,
            self.registration_completed_bar_at,
            self.first_shadow_eligible_at,
        )
        hashes = (
            self.model_sha256,
            self.prompt_sha256,
            self.code_sha256,
            self.data_manifest_sha256,
            self.protocol_sha256,
        )
        parameter_names = tuple(parameter.name for parameter in self.free_parameters)
        if (
            _HEX64.fullmatch(self.hypothesis_version_id) is None
            or _HEX64.fullmatch(self.family_id) is None
            or (self.parent_version_id is not None and _HEX64.fullmatch(self.parent_version_id) is None)
            or not _canonical_text(self.universe_snapshot_id)
            or not _sorted_unique_text(self.source_refs)
            or not _sorted_unique_text(self.methodology_tags)
            or not _canonical_text(self.primary_evaluation_owner)
            or not _canonical_text(self.evaluation_cadence)
            or not all(
                _canonical_text(value)
                for value in (
                    self.predictor,
                    self.target,
                    self.entry_rule,
                    self.exit_rule,
                    self.stop_rule,
                    self.invalidation_rule,
                    self.multiple_testing_family,
                )
            )
            or not _finite_decimal(self.threshold)
            or parameter_names != tuple(sorted(set(parameter_names)))
            or not all(_HEX64.fullmatch(value) for value in hashes)
            or not all(aware(value) for value in times)
            or not (
                self.universe_snapshot_at
                <= self.sampling_timestamp
                <= self.created_at
                <= self.registration_completed_bar_at
                < self.first_shadow_eligible_at
            )
        ):
            raise InvalidDayHypothesisModelError("invalid day hypothesis version")
        if self.hypothesis_version_id != self.canonical_id_for(self.model_dump(mode="python")):
            raise InvalidDayHypothesisModelError("hypothesis_version_id_mismatch")
        return self


def _canonical_identity(payload: Mapping[str, object], identity_field: str) -> str:
    canonical_payload = _semantic_value({key: value for key, value in payload.items() if key != identity_field})
    canonical_json = json.dumps(canonical_payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical_json.encode()).hexdigest()


def _semantic_value(value: object) -> object:
    match value:
        case BaseModel() as model:
            return _semantic_value(model.model_dump(mode="python"))
        case Mapping() as mapping:
            return {str(key): _semantic_value(item) for key, item in mapping.items()}
        case list() | tuple() as values:
            return [_semantic_value(item) for item in values]
        case dt.datetime() as timestamp:
            return _normalize_datetime(timestamp).isoformat(timespec="microseconds").replace("+00:00", "Z")
        case dt.date() as date:
            return date.isoformat()
        case dt.timedelta() as duration:
            return (duration.days, duration.seconds, duration.microseconds)
        case Decimal() as decimal:
            return format(_normalize_decimal(decimal), "f")
        case StrEnum() as member:
            return member.value
        case None | bool() | int() | float() | str():
            return value
        case unsupported:
            raise TypeError(f"unsupported canonical value: {type(unsupported).__name__}")


def _normalize_datetime(value: dt.datetime) -> dt.datetime:
    return value.astimezone(dt.UTC) if aware(value) else value


def _normalize_decimal(value: Decimal) -> Decimal:
    if not value.is_finite() or value.is_zero():
        return Decimal(0) if value.is_zero() else value
    text = format(value, "f")
    if "." not in text:
        return Decimal(text)
    integer, fraction = text.split(".", maxsplit=1)
    trimmed_fraction = fraction.rstrip("0")
    return Decimal(integer if not trimmed_fraction else f"{integer}.{trimmed_fraction}")


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip()


def _finite_decimal(value: Decimal) -> bool:
    return value.is_finite()


def _nonnegative_finite_decimal(value: Decimal) -> bool:
    return _finite_decimal(value) and value >= Decimal(0)


def _sorted_unique_decimals(values: tuple[Decimal, ...]) -> bool:
    return bool(values) and all(_finite_decimal(value) for value in values) and values == tuple(sorted(set(values)))


def _sorted_unique_text(values: tuple[str, ...]) -> bool:
    return bool(values) and all(_canonical_text(value) for value in values) and values == tuple(sorted(set(values)))
