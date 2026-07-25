from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal, override

from pydantic import BaseModel, ConfigDict, SecretStr

JobRow = tuple[str, int | None, int]
AgentId = Literal[
    "kr-theme",
    "us-intraday",
    "us-systematic",
    "us-swing",
    "research",
    "delivery",
]


class MarketView(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: Literal["kr", "us"]
    label: str
    local_time: dt.datetime
    state: Literal["open", "closed", "pre", "after"]


class ForwardView(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_date: dt.date | None
    eligible: bool
    ranking_cycles: int
    watch_cycles: int
    failed_watch_cycles: int
    read_retries: int
    read_retry_failures: int
    candidate_input_cycles: int
    candidate_inputs: int
    recommendations: int
    blockers: tuple[str, ...]
    incidents: tuple[str, ...]


class AgentView(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: AgentId
    label: str
    state: Literal["running", "armed", "idle", "failed"]
    scheduled_label: str


class RecommendationView(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    strategy: str
    created_at: dt.datetime
    entry: float
    stop: float
    target_1r: float
    target_2r: float
    state: str
    rationale: str


class SignalView(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    side: str
    strategy: str
    observed_at: dt.datetime
    valid_until: dt.datetime
    entry_price: str
    stop_price: str
    targets: tuple[str, ...]
    actionability: str
    rationale: str
    evidence_namespaces: tuple[str, ...]


class ResearchView(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ready", "blocked", "pending", "unavailable"]
    session_date: dt.date | None
    summary: str


class DashboardSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    generated_at: dt.datetime
    source: Literal["local-runtime"] = "local-runtime"
    markets: tuple[MarketView, ...]
    forward: ForwardView
    agents: tuple[AgentView, ...]
    recommendations: tuple[RecommendationView, ...]
    signals: tuple[SignalView, ...]
    research: ResearchView


@dataclass(frozen=True, slots=True)
class DashboardCredentialError(PermissionError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class DashboardCredentials:
    dashboard_url: str
    ingest_token: SecretStr = field(repr=False)
