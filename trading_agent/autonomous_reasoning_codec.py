from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from pydantic import ValidationError

from trading_agent.researcher_llm import LlmProposalClient, ResearcherLlmError

_MAX_PROMPT_BYTES = 256 * 1024

type JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]

if TYPE_CHECKING:
    from trading_agent.autonomous_reasoning import AutonomousReasoningRequest, AutonomousReasoningResponse


def canonical_reasoning_prompt(request: AutonomousReasoningRequest, client: LlmProposalClient | None) -> str:
    from trading_agent.autonomous_reasoning import InvalidAutonomousReasoningError

    provider = {"model_id": "unbound", "seed": None, "temperature": 0.0}
    if client is not None:
        provider = {"model_id": client.model_id, "seed": client.seed, "temperature": client.temperature}
    payload = {
        "allowed_tool_names": request.allowed_tool_names,
        "allowed_tool_signatures": request.allowed_tool_signatures,
        "current_role": None if request.current_role is None else request.current_role.value,
        "memories": tuple(memory.model_dump(mode="json") for memory in request.memories),
        "now": request.now.isoformat(),
        "observations": tuple(observation.model_dump(mode="json") for observation in request.observations),
        "prior_steps": tuple(step.model_dump(mode="json") for step in request.prior_steps),
        "provider": provider,
        "remaining_budget": request.remaining_budget.model_dump(mode="json"),
        "response_schema": _autonomous_response_schema(),
        "schema_version": 2,
        "task": request.task.model_dump(mode="json"),
    }
    prompt = json.dumps(payload, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    if len(prompt.encode()) > _MAX_PROMPT_BYTES:
        raise InvalidAutonomousReasoningError(reason="autonomous_reasoning_prompt_invalid")
    return prompt


def _autonomous_response_schema() -> dict[str, JsonValue]:
    from trading_agent.autonomous_reasoning import (
        AUTONOMOUS_REASONING_RESPONSE_ADAPTER,
        InvalidAutonomousReasoningError,
    )

    schema = AUTONOMOUS_REASONING_RESPONSE_ADAPTER.json_schema()
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        raise InvalidAutonomousReasoningError(reason="autonomous_reasoning_prompt_invalid")
    defer = definitions.get("AutonomousDefer")
    if not isinstance(defer, dict):
        raise InvalidAutonomousReasoningError(reason="autonomous_reasoning_prompt_invalid")
    properties = defer.get("properties")
    if not isinstance(properties, dict):
        raise InvalidAutonomousReasoningError(reason="autonomous_reasoning_prompt_invalid")
    wake_at = _non_null_property_schema(properties, "next_wake_at")
    wake_event = _non_null_property_schema(properties, "next_wake_event")
    at_branch: dict[str, JsonValue] = {
        "properties": {
            "next_wake_at": wake_at,
            "next_wake_event": {"type": "null"},
        },
        "required": ["next_wake_at", "next_wake_event"],
    }
    event_branch: dict[str, JsonValue] = {
        "properties": {
            "next_wake_at": {"type": "null"},
            "next_wake_event": wake_event,
        },
        "required": ["next_wake_at", "next_wake_event"],
    }
    defer["oneOf"] = list[JsonValue]((at_branch, event_branch))
    return schema


def _non_null_property_schema(
    properties: dict[str, JsonValue],
    name: str,
) -> dict[str, JsonValue]:
    from trading_agent.autonomous_reasoning import InvalidAutonomousReasoningError

    property_schema = properties.get(name)
    if isinstance(property_schema, dict):
        choices = property_schema.get("anyOf")
        if isinstance(choices, list):
            for choice in choices:
                if isinstance(choice, dict) and choice.get("type") != "null":
                    return dict(choice)
    raise InvalidAutonomousReasoningError(reason="autonomous_reasoning_prompt_invalid")


@dataclass(frozen=True, slots=True)
class AutonomousStructuredReasoner:
    client: LlmProposalClient

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        from trading_agent.autonomous_reasoning import (
            AUTONOMOUS_REASONING_RESPONSE_ADAPTER,
            InvalidAutonomousReasoningError,
        )

        try:
            raw = self.client.complete(canonical_reasoning_prompt(request, self.client))
            if len(raw) > 32_768:
                raise InvalidAutonomousReasoningError(reason="autonomous_reasoning_response_invalid")
            response = AUTONOMOUS_REASONING_RESPONSE_ADAPTER.validate_json(raw)
        except (InvalidAutonomousReasoningError, ResearcherLlmError, UnicodeError, ValidationError):
            raise InvalidAutonomousReasoningError(reason="autonomous_reasoning_response_invalid") from None
        validate_reasoning_response(request, response)
        return response


def validate_reasoning_response(request: AutonomousReasoningRequest, response: AutonomousReasoningResponse) -> None:
    from trading_agent.autonomous_reasoning import (
        AutonomousComplete,
        AutonomousDefer,
        AutonomousDelegate,
        AutonomousRecordMemory,
        AutonomousSubmitArtifact,
        AutonomousToolCall,
        InvalidAutonomousReasoningError,
    )

    match response:
        case AutonomousToolCall(tool_name=tool_name) if tool_name not in request.allowed_tool_names:
            raise InvalidAutonomousReasoningError(reason="autonomous_tool_not_allowed")
        case AutonomousDelegate(role=role) if role is request.current_role:
            raise InvalidAutonomousReasoningError(reason="autonomous_delegate_role_denied")
        case AutonomousDefer(next_wake_at=wake_at) if wake_at is not None and wake_at <= request.now:
            raise InvalidAutonomousReasoningError(reason="autonomous_response_wake_not_future")
        case AutonomousSubmitArtifact(next_wake_at=wake_at) if wake_at is not None and wake_at <= request.now:
            raise InvalidAutonomousReasoningError(reason="autonomous_response_wake_not_future")
        case (
            AutonomousToolCall()
            | AutonomousDelegate()
            | AutonomousSubmitArtifact()
            | AutonomousRecordMemory()
            | AutonomousDefer()
            | AutonomousComplete()
        ):
            return
        case unreachable:
            assert_never(unreachable)


def require_sorted_unique(values: tuple[str, ...], *, reason: str) -> None:
    from trading_agent.autonomous_reasoning import InvalidAutonomousReasoningError

    if values != tuple(sorted(set(values))) or any(not value for value in values):
        raise InvalidAutonomousReasoningError(reason=reason)


def require_canonical_json(value: str, *, reason: str) -> None:
    from trading_agent.autonomous_reasoning import InvalidAutonomousReasoningError

    try:
        decoded = json.loads(value)
        canonical = json.dumps(decoded, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        raise InvalidAutonomousReasoningError(reason=reason) from None
    if canonical != value or len(value.encode()) > 16_384:
        raise InvalidAutonomousReasoningError(reason=reason)
