from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Self, assert_never

from pydantic import Field, model_validator

from trading_agent.strategy_research_types import (
    AttemptStatus,
    CanonicalModel,
    ResearchAgentId,
    SafeTerminalReason,
    StrategyResearchContractError,
    TerminalOutcome,
    aware,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SHA256 = re.compile(_SHA256_PATTERN)
_SAFE_ARTIFACT_REF = re.compile(r"^artifact://safe/[0-9a-f]{64}$")


class ResearchAttempt(CanonicalModel):
    attempt_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    branch_index: int = Field(ge=0)
    input_hashes: tuple[str, ...] = Field(min_length=1)
    code_sha256: str = Field(pattern=_SHA256_PATTERN)
    data_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    started_at: dt.datetime
    finished_at: dt.datetime | None
    status: AttemptStatus
    artifact_refs: tuple[str, ...]
    error_class: str | None
    max_cpu_seconds: int = Field(ge=1, le=86_400)

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        if (
            not aware(self.started_at)
            or any(_SHA256.fullmatch(value) is None for value in self.input_hashes)
            or len(set(self.input_hashes)) != len(self.input_hashes)
            or (self.finished_at is not None and (not aware(self.finished_at) or self.finished_at < self.started_at))
        ):
            raise StrategyResearchContractError
        match self.status:
            case AttemptStatus.STARTED:
                if self.finished_at is not None or self.artifact_refs or self.error_class is not None:
                    raise StrategyResearchContractError
            case AttemptStatus.SUCCEEDED:
                if self.finished_at is None or not self.artifact_refs or self.error_class is not None:
                    raise StrategyResearchContractError
            case (
                AttemptStatus.FAILED
                | AttemptStatus.ABORTED
                | AttemptStatus.TIMED_OUT
                | AttemptStatus.CANCELLED
                | AttemptStatus.CENSORED
            ):
                if self.finished_at is None or not self.error_class:
                    raise StrategyResearchContractError
            case unreachable:
                assert_never(unreachable)
        return self


class TerminalResearchResult(CanonicalModel):
    result_id: str = Field(min_length=1)
    hypothesis_id: str = Field(min_length=1)
    owner_agent_id: ResearchAgentId
    outcome: TerminalOutcome
    reason_codes: tuple[SafeTerminalReason, ...] = Field(min_length=1)
    artifact_refs: tuple[str, ...] = Field(min_length=1)
    evaluated_at: dt.datetime
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if (
            not aware(self.evaluated_at)
            or len(set(self.reason_codes)) != len(self.reason_codes)
            or len(set(self.artifact_refs)) != len(self.artifact_refs)
            or any(_SAFE_ARTIFACT_REF.fullmatch(value) is None for value in self.artifact_refs)
        ):
            raise StrategyResearchContractError
        return self


__all__ = ("ResearchAttempt", "TerminalResearchResult")
