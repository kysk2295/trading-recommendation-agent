from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, override

from pydantic import JsonValue, TypeAdapter, ValidationError

from trading_agent.day_agent_tool_models import DayAgentReasoningRequest, DayAgentReasoningResponse
from trading_agent.researcher_llm import LlmProposalClient, ResearcherLlmError
from trading_agent.us_day_thesis_runtime import us_day_thesis_response_schema

_MAX_RESPONSE_BYTES = 256 * 1024
_DAY_RESPONSE = TypeAdapter(DayAgentReasoningResponse)
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class UsDayLiveModelError(ValueError):
    @override
    def __str__(self) -> str:
        return "us_day_live_model_response_invalid"


@dataclass(frozen=True, slots=True)
class UsDayStructuredReasoner:
    client: LlmProposalClient
    role: Literal["reasoning", "coding"] = "reasoning"

    def next_step(self, request: DayAgentReasoningRequest) -> DayAgentReasoningResponse:
        prompt = _prompt(
            instruction=(
                "Return exactly one JSON object matching the response schema. Use only the supplied "
                "request and observations. Choose only an allowed tool, or submit a thesis, or defer."
            ),
            request=request.model_dump(mode="json"),
            response_schema=_DAY_RESPONSE.json_schema(),
        )
        try:
            return _DAY_RESPONSE.validate_json(_completion(self.client, prompt))
        except (TypeError, ValidationError, ValueError):
            raise UsDayLiveModelError from None


@dataclass(frozen=True, slots=True)
class UsDayStructuredThesisReasoner:
    client: LlmProposalClient

    def __call__(self, request: Mapping[str, object]) -> Mapping[str, object]:
        prompt = _prompt(
            instruction=(
                "Return exactly one JSON object and no prose. Use only supplied evidence. Preserve every "
                "identity and timestamp exactly. For a non-recommendation, use null prices and rationales."
            ),
            request=request,
            response_schema=us_day_thesis_response_schema(),
        )
        try:
            return _JSON_OBJECT.validate_json(_completion(self.client, prompt))
        except (TypeError, ValidationError, ValueError):
            raise UsDayLiveModelError from None


def _completion(client: LlmProposalClient, prompt: str) -> bytes:
    try:
        response = client.complete(prompt)
    except ResearcherLlmError:
        raise UsDayLiveModelError from None
    if not response or len(response) > _MAX_RESPONSE_BYTES:
        raise UsDayLiveModelError
    return response


def _prompt(
    *,
    instruction: str,
    request: Mapping[str, object],
    response_schema: Mapping[str, object],
) -> str:
    try:
        return json.dumps(
            {
                "instruction": instruction,
                "request": request,
                "response_schema": response_schema,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        raise UsDayLiveModelError from None


__all__ = (
    "UsDayLiveModelError",
    "UsDayStructuredReasoner",
    "UsDayStructuredThesisReasoner",
)
