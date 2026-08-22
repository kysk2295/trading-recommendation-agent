from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from tests.day_agent_support import day_task
from tests.test_day_agent_runtime import _thesis_call
from trading_agent.day_agent_tool_models import DayAgentReasoningRequest
from trading_agent.researcher_llm import LlmProposalClient
from trading_agent.us_day_live_models import (
    UsDayLiveModelError,
    UsDayStructuredReasoner,
    UsDayStructuredThesisReasoner,
)


@dataclass(slots=True)
class _RecordingClient:
    response: bytes
    model_id: str = "reasoner-v1"
    seed: int | None = None
    temperature: float = 0.0
    prompts: list[str] = field(default_factory=list)

    def complete(self, prompt: str) -> bytes:
        self.prompts.append(prompt)
        return self.response


def _request() -> DayAgentReasoningRequest:
    task = day_task()
    return DayAgentReasoningRequest(
        task=task,
        prior_steps=(),
        observations=(),
        allowed_tool_names=(),
        remaining_budget=task.budget,
    )


def test_day_reasoner_parses_one_strict_json_response() -> None:
    # Given: a model client returning one schema-valid JSON object.
    client: LlmProposalClient = _RecordingClient(_thesis_call().model_dump_json().encode())

    # When: the live Day reasoning adapter completes a step.
    response = UsDayStructuredReasoner(client).next_step(_request())

    # Then: the typed response is returned and the request was sent once.
    assert response == _thesis_call()
    assert len(client.prompts) == 1


def test_thesis_reasoner_returns_only_a_json_object() -> None:
    # Given: a model client returning a bounded JSON mapping.
    client: LlmProposalClient = _RecordingClient(b'{"decision":"no_trade"}')

    # When: the structured thesis adapter is called.
    response = UsDayStructuredThesisReasoner(client)({"situation": {"session_id": "XNYS-2026-08-21"}})

    # Then: callers receive the exact parsed mapping.
    assert response == {"decision": "no_trade"}
    assert len(client.prompts) == 1
    prompt = json.loads(client.prompts[0])
    assert set(prompt["response_schema"]["required"]) >= {
        "decision",
        "situation_id",
        "agent_version_id",
        "entry_price",
        "stop_price",
        "targets",
        "valid_until",
    }


@pytest.mark.parametrize("response", (b"```json\n{}\n```", b"[]", b"not-json"))
def test_live_model_adapter_fails_closed_for_non_protocol_output(response: bytes) -> None:
    # Given: output that is not the requested strict response object.
    client: LlmProposalClient = _RecordingClient(response)

    # When / Then: the boundary rejects it without repair or prose extraction.
    with pytest.raises(UsDayLiveModelError, match="us_day_live_model_response_invalid"):
        _ = UsDayStructuredReasoner(client).next_step(_request())
