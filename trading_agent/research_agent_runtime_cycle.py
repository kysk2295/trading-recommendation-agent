from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Literal, Protocol

import anyio

from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.research_agent_runtime_models import (
    InvalidResearchAgentRuntimeError,
    ResearchAgentBoundedCycleResult,
    ResearchAgentTickResult,
)


class TickRuntime(Protocol):
    def tick(self, now: dt.datetime) -> ResearchAgentTickResult: ...


def bounded_cycle(
    run_family: Callable[[AgentFamilyId], ResearchAgentTickResult],
) -> ResearchAgentBoundedCycleResult:
    outcomes = tuple(
        outcome
        for family in PRIMARY_AGENT_FAMILIES
        if (outcome := run_family(family)).status != "idle"
    )
    families = tuple(item.agent_family_id for item in outcomes)
    status: Literal["idle", "partial", "complete"]
    if not families:
        status = "idle"
    elif families == PRIMARY_AGENT_FAMILIES:
        status = "complete"
    else:
        status = "partial"
    return ResearchAgentBoundedCycleResult(
        status=status,
        outcomes=outcomes,
        model_calls=sum(item.model_calls for item in outcomes),
        recovered_cycles=sum(item.recovered_cycles for item in outcomes),
    )


async def run_research_agent_forever(
    runtime: TickRuntime,
    clock: Callable[[], dt.datetime],
    tick_seconds: float = 30.0,
) -> None:
    if tick_seconds <= 0:
        raise InvalidResearchAgentRuntimeError(reason="tick_seconds_invalid")
    while True:
        _ = runtime.tick(clock())
        await anyio.sleep(tick_seconds)


__all__ = ("bounded_cycle", "run_research_agent_forever")
