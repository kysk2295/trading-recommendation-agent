from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal, Protocol, Self, override

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.experiment_ledger_models import ResearchSource
from trading_agent.lane_identity_models import LaneId


class ResearcherLlmError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "structured researcher LLM call failed closed"


class ResearcherContextInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    lane_id: LaneId
    sources: tuple[ResearchSource, ...]
    regime_context: str = Field(min_length=1, max_length=4_096)

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        source_ids = tuple(source.source_id for source in self.sources)
        if not source_ids or source_ids != tuple(sorted(set(source_ids))):
            raise ResearcherLlmError
        return self


class LlmHypothesisDraft(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    hypothesis_id: str = Field(min_length=1, max_length=128)
    hypothesis: str = Field(min_length=1, max_length=4_096)
    falsification_rule: str = Field(min_length=1, max_length=4_096)
    cited_source_ids: tuple[str, ...]
    economic_mechanism: str = Field(min_length=1, max_length=4_096)
    counterfactual_baseline: str = Field(min_length=1, max_length=4_096)
    strategy_source: str = Field(min_length=1, max_length=64 * 1024)
    free_parameters: tuple[str, ...]
    methodology_tags: tuple[str, ...] = ()

    @field_validator("cited_source_ids", "free_parameters", "methodology_tags", mode="after")
    @classmethod
    def canonicalize_sets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def validate_citations(self) -> Self:
        if not self.cited_source_ids:
            raise ResearcherLlmError
        return self


class LlmProposalClient(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def seed(self) -> int | None: ...

    @property
    def temperature(self) -> float: ...

    def complete(self, prompt: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class ResearcherLlmPlan:
    prompt: str
    prompt_sha256: str
    prompt_bytes_sha256: str
    model_id: str
    seed: int | None
    temperature: float
    protocol_sha256: str
    creator: str
    creator_sha256: str
    planned_at: dt.datetime


@dataclass(frozen=True, slots=True)
class ResearcherRawCompletion:
    response: bytes
    response_sha256: str
    response_length: int
    invocation_started_at: dt.datetime
    received_at: dt.datetime


@dataclass(frozen=True, slots=True)
class FixtureLlmProposalClient:
    response: bytes
    model_id: str = "fixture-researcher-v1"
    seed: int | None = 7
    temperature: float = 0.0

    def complete(self, prompt: str) -> bytes:
        del prompt
        return self.response


__all__ = (
    "FixtureLlmProposalClient",
    "LlmHypothesisDraft",
    "LlmProposalClient",
    "ResearcherContextInput",
    "ResearcherLlmError",
    "ResearcherLlmPlan",
    "ResearcherRawCompletion",
)
