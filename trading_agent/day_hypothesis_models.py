from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import to_jsonable_python

from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_models import SearchBudget, TargetHorizon
from trading_agent.strategy_research_types import ExpectedDirection, aware

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class InvalidDayHypothesisModelError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class MethodologyDeclaration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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


class CostModelDeclaration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: str
    commission_bps: Decimal
    slippage_bps: Decimal

    @model_validator(mode="after")
    def validate_cost_model(self) -> Self:
        if (
            not _canonical_text(self.model_id)
            or not _nonnegative_finite_decimal(self.commission_bps)
            or not _nonnegative_finite_decimal(self.slippage_bps)
        ):
            raise InvalidDayHypothesisModelError("invalid cost model declaration")
        return self


class FreeParameter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    values: tuple[Decimal, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_parameter(self) -> Self:
        if not _canonical_text(self.name) or not _sorted_unique_decimals(self.values):
            raise InvalidDayHypothesisModelError("invalid free parameter")
        return self


class HypothesisFamily(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    family_id: str
    parent_family_id: str | None
    canonical_question: str
    economic_mechanism: str
    alternative_explanations: tuple[str, ...] = Field(min_length=1)
    counterfactual_baseline: str
    created_by: str
    created_at: dt.datetime
    source_lineage: tuple[str, ...] = Field(min_length=1)

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


class HypothesisVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

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
    free_parameters: tuple[FreeParameter, ...] = Field(min_length=1, max_length=12)
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
    encoded_payload = to_jsonable_python({key: value for key, value in payload.items() if key != identity_field})
    canonical_json = json.dumps(encoded_payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical_json.encode()).hexdigest()


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


__all__ = (
    "CostModelDeclaration",
    "FreeParameter",
    "HypothesisFamily",
    "HypothesisVersion",
    "InvalidDayHypothesisModelError",
    "MethodologyDeclaration",
    "SearchBudget",
)
