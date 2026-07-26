from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_directed_jobs import DirectedJobEvent
from trading_agent.dashboard_hermes_protocol import MAX_HERMES_RESPONSE_CHARS

InteractionMode = Literal[
    "conversation",
    "research",
    "analysis",
    "hypothesis",
    "experiment",
    "allowed_code",
]


class InteractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    agent_id: AgentFamilyId
    mode: InteractionMode
    command: str = Field(min_length=1, max_length=2_000)
    state: Literal["queued", "running"]
    response: None
    created_at: datetime
    updated_at: datetime


class DashboardInteractionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["interaction"]
    interaction: InteractionPayload


class PairingTicketMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["pairing_ticket"]
    path: str = Field(pattern=r"^/operator/pair/[A-Za-z0-9_-]{40,}$")


type DashboardEvent = Annotated[
    DashboardInteractionMessage | PairingTicketMessage,
    Field(discriminator="type"),
]


class InteractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["interaction_result"] = "interaction_result"
    interaction_id: str
    state: Literal["running", "completed", "failed", "uncertain"]
    response: str | None = Field(max_length=MAX_HERMES_RESPONSE_CHARS)


@dataclass(frozen=True, slots=True)
class InteractionExecution:
    result: InteractionResult
    directed_events: tuple[DirectedJobEvent, ...]
    process_started: bool


_event_adapter = TypeAdapter(DashboardEvent)


def parse_dashboard_event(raw: str) -> DashboardEvent:
    return _event_adapter.validate_json(raw)


__all__ = (
    "DashboardInteractionMessage",
    "InteractionExecution",
    "InteractionPayload",
    "InteractionResult",
    "PairingTicketMessage",
    "parse_dashboard_event",
)
