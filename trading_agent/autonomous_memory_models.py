from __future__ import annotations

import datetime as dt
import hashlib
import json
from enum import StrEnum, unique
from typing import Annotated, Final, Literal, NewType, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from trading_agent.autonomous_task_models import AutonomousTaskId

MemoryId = NewType("MemoryId", str)
_SHA256: Final = r"^[a-f0-9]{64}$"
_MemoryHash = Annotated[MemoryId, Field(pattern=_SHA256)]
_TaskHash = Annotated[AutonomousTaskId, Field(pattern=_SHA256)]
_MemoryKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,159}$")]


@unique
class AutonomousMemoryScope(StrEnum):
    WORK = "work"
    MARKET = "market"
    STRATEGY = "strategy"
    SELF_IMPROVEMENT = "self_improvement"


class AutonomousMemoryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    memory_id: _MemoryHash = MemoryId("")
    memory_key: _MemoryKey
    version: int = Field(ge=1)
    scope: AutonomousMemoryScope
    summary: str = Field(min_length=8, max_length=4_000)
    fact_refs: tuple[str, ...] = Field(default=(), max_length=64)
    inference_refs: tuple[str, ...] = Field(default=(), max_length=64)
    subject_refs: tuple[str, ...] = Field(default=(), max_length=32)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=128)
    source_task_ids: tuple[_TaskHash, ...] = Field(min_length=1, max_length=32)
    recorded_at: AwareDatetime

    @field_validator("fact_refs", "inference_refs", "subject_refs", "evidence_refs", "source_task_ids")
    @classmethod
    def require_sorted_unique_refs(cls, value: tuple[str, ...], info: ValidationInfo) -> tuple[str, ...]:
        if any(not reference for reference in value) or tuple(sorted(value)) != value or len(set(value)) != len(value):
            raise ValueError(f"sorted_unique_{info.field_name}_required")
        return value

    @field_validator("recorded_at", mode="after")
    @classmethod
    def normalize_recorded_at(cls, value: dt.datetime) -> dt.datetime:
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def validate_identity_and_lineage(self) -> Self:
        if not self.fact_refs and not self.inference_refs:
            raise ValueError("memory_lineage_required")
        expected = autonomous_memory_id(self)
        if self.memory_id == MemoryId(""):
            return self.model_copy(update={"memory_id": expected})
        if self.memory_id != expected:
            raise ValueError("memory_id_mismatch")
        return self


def autonomous_memory_payload(record: AutonomousMemoryRecord) -> str:
    return json.dumps(
        record.model_dump(mode="json", exclude={"memory_id"}), ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )


def autonomous_memory_id(record: AutonomousMemoryRecord) -> MemoryId:
    return MemoryId(hashlib.sha256(autonomous_memory_payload(record).encode()).hexdigest())
