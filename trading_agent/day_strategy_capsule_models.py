from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.day_hypothesis_models import CostModelDeclaration
from trading_agent.generated_strategy_execution import GeneratedStrategyLimits
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_types import aware

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ARTIFACT_REF = re.compile(r"^artifact://safe/[0-9a-f]{64}$")


class InvalidStrategyCapsuleError(ValueError):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


class CapsuleArtifactKind(StrEnum):
    BUILTIN = "builtin"
    GENERATED_PYTHON = "generated_python"


class CapsuleAuthorityCeiling(StrEnum):
    RESEARCH_ONLY = "research_only"
    US_ALPACA_PAPER_CAPABLE = "us_alpaca_paper_capable"


class CapsuleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always", strict=True)

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        payload = self.model_dump(mode="python")
        if update is not None:
            payload.update(update)
        return self.__class__.model_validate(payload)


class CapsuleResourceLimits(CapsuleModel):
    wall_seconds: float = Field(default=2.0, ge=0.05, le=30.0)
    cpu_seconds: int = Field(default=2, ge=1, le=30)
    rss_bytes: int = Field(default=1024 * 1024 * 1024, ge=128 * 1024 * 1024, le=10 * 1024 * 1024 * 1024)
    open_files: int = Field(default=32, ge=16, le=128)
    output_bytes: int = Field(default=1024 * 1024, ge=64 * 1024, le=8 * 1024 * 1024)

    def to_generated_limits(self) -> GeneratedStrategyLimits:
        return GeneratedStrategyLimits(**self.model_dump())


class CapsulePreflightReceipt(CapsuleModel):
    receipt_id: str
    generated_artifact_id: str
    runtime_fingerprint: str
    sandbox_profile_version: Literal["generated_strategy_sandbox_v1"]
    protocol_version: Literal[1]
    protocol_sha256: str
    evaluator_sha256: str
    resource_limits: CapsuleResourceLimits
    replay_input_sha256: str
    first_run_sha256: str
    second_run_sha256: str
    deterministic_replay_sha256: str
    successful: Literal[True]
    completed_at: dt.datetime
    trading_authority: Literal[False] = False

    @field_validator("completed_at")
    @classmethod
    def normalize_completed_at(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if aware(value) else value

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, object]) -> str:
        return _canonical_identity(payload, "receipt_id")

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        hashes = (
            self.receipt_id,
            self.generated_artifact_id,
            self.runtime_fingerprint,
            self.protocol_sha256,
            self.evaluator_sha256,
            self.replay_input_sha256,
            self.first_run_sha256,
            self.second_run_sha256,
            self.deterministic_replay_sha256,
        )
        expected_replay = hashlib.sha256(f"{self.first_run_sha256}:{self.second_run_sha256}".encode()).hexdigest()
        if (
            not all(_HEX64.fullmatch(value) for value in hashes)
            or not aware(self.completed_at)
            or self.completed_at.tzinfo is not dt.UTC
            or self.first_run_sha256 != self.second_run_sha256
            or self.deterministic_replay_sha256 != expected_replay
            or self.receipt_id != self.canonical_id_for(self.model_dump(mode="python"))
        ):
            raise InvalidStrategyCapsuleError("invalid_capsule_preflight_receipt")
        return self


class StrategyCapsule(CapsuleModel):
    capsule_id: str
    hypothesis_version_id: str
    attempt_binding_id: str
    market_id: MarketId
    artifact_kind: CapsuleArtifactKind
    artifact_ref: str
    artifact_sha256: str
    generated_artifact_id: str | None
    evaluation_cadence: str
    evidence_schema: tuple[str, ...] = Field(min_length=1)
    entry_rule: str
    exit_rule: str
    stop_rule: str
    target_rule: str
    cost_model: CostModelDeclaration
    slippage_model_id: str
    resource_limits: CapsuleResourceLimits
    risk_policy_ref: str
    protocol_version: Literal[1]
    protocol_sha256: str
    evaluator_sha256: str
    preflight_receipt: CapsulePreflightReceipt | None
    published_at: dt.datetime
    authority_ceiling: CapsuleAuthorityCeiling
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @field_validator("published_at")
    @classmethod
    def normalize_published_at(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC) if aware(value) else value

    @field_validator("trading_authority", mode="before")
    @classmethod
    def reject_authority(cls, value: bool) -> Literal[False]:
        if value is not False:
            raise InvalidStrategyCapsuleError("capsule_cannot_grant_authority")
        return False

    @field_validator("profitability_claim", mode="before")
    @classmethod
    def reject_profitability(cls, value: bool) -> Literal[False]:
        if value is not False:
            raise InvalidStrategyCapsuleError("capsule_cannot_claim_profitability")
        return False

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, object]) -> str:
        return _canonical_identity(payload, "capsule_id")

    @model_validator(mode="after")
    def validate_capsule(self) -> Self:
        hashes = (
            self.capsule_id,
            self.hypothesis_version_id,
            self.attempt_binding_id,
            self.artifact_sha256,
            self.protocol_sha256,
            self.evaluator_sha256,
        )
        text = (
            self.evaluation_cadence,
            self.entry_rule,
            self.exit_rule,
            self.stop_rule,
            self.target_rule,
            self.slippage_model_id,
            self.risk_policy_ref,
        )
        if self.market_id is MarketId.KR_EQUITIES and (
            self.authority_ceiling is CapsuleAuthorityCeiling.US_ALPACA_PAPER_CAPABLE
        ):
            raise InvalidStrategyCapsuleError("kr_capsule_authority_ceiling")
        if (
            not all(_HEX64.fullmatch(value) for value in hashes)
            or _ARTIFACT_REF.fullmatch(self.artifact_ref) is None
            or self.artifact_ref != f"artifact://safe/{self.artifact_sha256}"
            or not all(_canonical_text(value) for value in text)
            or not _sorted_unique_text(self.evidence_schema)
            or not aware(self.published_at)
            or self.published_at.tzinfo is not dt.UTC
        ):
            raise InvalidStrategyCapsuleError("invalid_strategy_capsule")
        match self.artifact_kind:
            case CapsuleArtifactKind.BUILTIN:
                if self.generated_artifact_id is not None or self.preflight_receipt is not None:
                    raise InvalidStrategyCapsuleError("builtin_capsule_generated_fields_forbidden")
            case CapsuleArtifactKind.GENERATED_PYTHON:
                receipt = self.preflight_receipt
                if (
                    self.generated_artifact_id is None
                    or _HEX64.fullmatch(self.generated_artifact_id) is None
                    or receipt is None
                    or receipt.generated_artifact_id != self.generated_artifact_id
                    or receipt.protocol_sha256 != self.protocol_sha256
                    or receipt.evaluator_sha256 != self.evaluator_sha256
                    or receipt.resource_limits != self.resource_limits
                    or receipt.completed_at > self.published_at
                ):
                    raise InvalidStrategyCapsuleError("generated_capsule_preflight_invalid")
        if self.capsule_id != self.canonical_id_for(self.model_dump(mode="python")):
            raise InvalidStrategyCapsuleError("capsule_id_mismatch")
        return self


def _canonical_identity(payload: Mapping[str, object], identity_field: str) -> str:
    normalized = _canonical_value({key: value for key, value in payload.items() if key != identity_field})
    encoded = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _canonical_value(value: object) -> object:
    match value:
        case BaseModel() as model:
            return _canonical_value(model.model_dump(mode="python"))
        case Mapping() as mapping:
            return {str(key): _canonical_value(item) for key, item in mapping.items()}
        case list() | tuple() as values:
            return [_canonical_value(item) for item in values]
        case dt.datetime() as timestamp:
            return timestamp.astimezone(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        case Decimal() as decimal:
            return format(decimal.normalize(), "f")
        case StrEnum() as member:
            return member.value
        case None | bool() | int() | float() | str():
            return value
        case unsupported:
            raise TypeError(f"unsupported capsule identity value: {type(unsupported).__name__}")


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip()


def _sorted_unique_text(values: tuple[str, ...]) -> bool:
    return bool(values) and values == tuple(sorted(set(values))) and all(_canonical_text(value) for value in values)


__all__ = (
    "CapsuleArtifactKind",
    "CapsuleAuthorityCeiling",
    "CapsulePreflightReceipt",
    "CapsuleResourceLimits",
    "InvalidStrategyCapsuleError",
    "StrategyCapsule",
)
