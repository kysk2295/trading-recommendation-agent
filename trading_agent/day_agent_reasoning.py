from __future__ import annotations

from typing import Literal, Protocol

from trading_agent.day_agent_tool_models import DayAgentReasoningRequest, DayAgentReasoningResponse


class DayAgentReasoningClient(Protocol):
    @property
    def role(self) -> Literal["reasoning", "coding"]: ...

    def next_step(self, request: DayAgentReasoningRequest) -> DayAgentReasoningResponse: ...


__all__ = ("DayAgentReasoningClient",)
