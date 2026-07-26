from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from trading_agent.dashboard_agent_family import AGENT_FAMILY_REGISTRY, AgentFamilyId

MAX_HERMES_RESPONSE_CHARS = 8_000
DirectedPlanKind = Literal["research", "analysis", "hypothesis", "experiment", "allowed_code"]


class HermesDelta(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event: Literal["delta"]
    text: str = Field(max_length=MAX_HERMES_RESPONSE_CHARS)


class HermesComplete(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event: Literal["complete"]
    text: str = Field(min_length=1, max_length=MAX_HERMES_RESPONSE_CHARS)
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    failed: bool
    error: str | None = Field(max_length=240)


class HermesDirectedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    operation: DirectedPlanKind
    intent: str = Field(min_length=1, max_length=240)


type HermesEvent = Annotated[HermesDelta | HermesComplete, Field(discriminator="event")]

_adapter = TypeAdapter(HermesEvent)
_family_definitions = {item.family_id: item for item in AGENT_FAMILY_REGISTRY}


def terminal_hermes_event(payload: bytes) -> HermesComplete:
    parsed = tuple(_adapter.validate_json(line) for line in payload.splitlines() if line.strip())
    terminals = tuple(item for item in parsed if isinstance(item, HermesComplete))
    if len(terminals) != 1 or parsed[-1] is not terminals[0]:
        raise ValidationError.from_exception_data("HermesStream", [])
    return terminals[0]


def hermes_interaction_argv(
    executable: str,
    family_id: AgentFamilyId,
    command: str,
    session_id: str | None,
) -> tuple[str, ...]:
    definition = _family_definitions[family_id]
    prompt = (
        f"<agent-family>{definition.family_id}</agent-family>\n"
        f"<memory-namespace>{definition.memory_namespace}</memory-namespace>\n"
        f"<role>{definition.role}</role>\n"
        "Follow AGENTS.md and repository policy. Return a concise evidence-bound Korean response. "
        "Never expose credentials, account identity, session identifiers, or local paths. "
        f"<operator-message>{command}</operator-message>"
    )
    base = (executable, "chat", "-Q", "--stream-json", "--source", "dashboard-command")
    resume = () if session_id is None else ("--resume", session_id)
    return (*base, *resume, "-q", prompt)


def hermes_directed_argv(
    executable: str,
    family_id: AgentFamilyId,
    command: str,
    operation: DirectedPlanKind,
    session_id: str | None,
) -> tuple[str, ...]:
    definition = _family_definitions[family_id]
    prompt = (
        f"<agent-family>{definition.family_id}</agent-family>\n"
        f"<memory-namespace>{definition.memory_namespace}</memory-namespace>\n"
        f"<role>{definition.role}</role>\n"
        "Follow AGENTS.md and repository policy. Return exactly one JSON object and no prose. "
        'The only fields are {"schema_version":1,"operation":"'
        f'{operation}","intent":"bounded summary"}}. '
        f"The operation must remain {operation}. Never emit or select argv, shell, executable, "
        "filesystem paths, provider mutation, Paper mutation, credentials, or session identifiers. "
        f"<operator-message>{command}</operator-message>"
    )
    base = (executable, "chat", "-Q", "--stream-json", "--source", "dashboard-command")
    resume = () if session_id is None else ("--resume", session_id)
    return (*base, *resume, "-q", prompt)


def parse_directed_plan(text: str, expected: DirectedPlanKind) -> HermesDirectedPlan:
    plan = HermesDirectedPlan.model_validate_json(text)
    if plan.operation != expected:
        raise ValidationError.from_exception_data("HermesDirectedPlan", [])
    return cast(HermesDirectedPlan, plan)


__all__ = (
    "MAX_HERMES_RESPONSE_CHARS",
    "DirectedPlanKind",
    "HermesComplete",
    "HermesDirectedPlan",
    "hermes_directed_argv",
    "hermes_interaction_argv",
    "parse_directed_plan",
    "terminal_hermes_event",
)
