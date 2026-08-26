from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from tests.test_autonomous_task_models import NOW
from trading_agent._autonomous_supervisor_steps import parse_payload
from trading_agent.autonomous_reasoning import (
    AUTONOMOUS_REASONING_RESPONSE_ADAPTER,
    AutonomousReasoningRequest,
    AutonomousReasoningResponse,
    AutonomousToolArguments,
)


@dataclass(frozen=True, slots=True, init=False)
class FakeReasoner:
    response_jsons: tuple[str, ...]
    priority_routes: bool

    def __init__(self, responses: tuple[AutonomousReasoningResponse, ...], priority_routes: bool = False) -> None:
        object.__setattr__(self, "response_jsons", tuple(response.model_dump_json() for response in responses))
        object.__setattr__(self, "priority_routes", priority_routes)

    def next_step(self, request: AutonomousReasoningRequest) -> AutonomousReasoningResponse:
        index = sum(parse_payload(step.payload_json).kind == "decision" for step in request.prior_steps)
        if self.priority_routes and request.task.priority != 90:
            index += 2
        return AUTONOMOUS_REASONING_RESPONSE_ADAPTER.validate_json(self.response_jsons[index])


def observed_tool(_args: AutonomousToolArguments) -> str:
    return '{"status":"observed"}'


def now_clock() -> dt.datetime:
    return NOW


def zero_clock() -> float:
    return 0.0


__all__ = ("FakeReasoner", "now_clock", "observed_tool", "zero_clock")
