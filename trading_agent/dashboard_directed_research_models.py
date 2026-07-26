from __future__ import annotations

import datetime as dt
from typing import Literal, Protocol, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.strategy_factory import StrategyMode

DirectedResearchKind = Literal["research", "analysis", "hypothesis", "experiment"]


class InvalidDirectedResearchBrokerError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "directed research broker failed closed"


class DirectedResearchReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: DirectedResearchKind
    terminal: Literal["completed"]
    domain_effects: int = Field(ge=1, le=32)
    evidence_sha256s: tuple[str, ...] = Field(min_length=1, max_length=32)
    result_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    summary: str = Field(min_length=1, max_length=240)


class DirectedExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    session_dates: tuple[dt.date, ...] = Field(min_length=1, max_length=32)
    required_session_dates: tuple[dt.date, ...] = Field(min_length=1, max_length=32)
    strategy: StrategyMode
    strategy_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    dataset_producer_commit_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    code_version: str = Field(pattern=r"^[a-f0-9]{40}$")
    registered_at: AwareDatetime
    observed_at: AwareDatetime
    minimum_clean_sessions: int = Field(ge=1, le=32)
    minimum_training_sessions: int = Field(ge=0, le=31)
    max_sessions: int = Field(ge=1, le=32)
    max_bars: int = Field(ge=1, le=100_000)
    per_side_fee_bps: int = Field(ge=0, le=1_000)
    per_side_slippage_bps: int = Field(ge=0, le=1_000)
    bootstrap_samples: int = Field(ge=100, le=10_000)
    rss_limit_gib: float = Field(gt=0, le=64)

    @model_validator(mode="after")
    def require_canonical_dates(self) -> Self:
        if (
            self.session_dates != tuple(sorted(set(self.session_dates)))
            or self.required_session_dates != tuple(sorted(set(self.required_session_dates)))
            or not set(self.required_session_dates).issubset(self.session_dates)
            or self.minimum_clean_sessions > self.max_sessions
            or self.observed_at < self.registered_at
        ):
            raise InvalidDirectedResearchBrokerError
        return self


class DirectedResearchBroker(Protocol):
    def execute(self, operation: DirectedResearchKind, family_id: AgentFamilyId) -> bytes: ...


def parse_directed_research_receipt(
    raw: bytes,
    expected: DirectedResearchKind,
) -> DirectedResearchReceipt:
    try:
        receipt = DirectedResearchReceipt.model_validate_json(raw)
    except ValidationError as error:
        raise InvalidDirectedResearchBrokerError from error
    if receipt.operation != expected:
        raise InvalidDirectedResearchBrokerError
    return receipt


__all__ = (
    "DirectedExperimentSpec",
    "DirectedResearchBroker",
    "DirectedResearchKind",
    "DirectedResearchReceipt",
    "InvalidDirectedResearchBrokerError",
    "parse_directed_research_receipt",
)
