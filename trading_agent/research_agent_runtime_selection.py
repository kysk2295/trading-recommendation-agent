from __future__ import annotations

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_cycle_models import ResearchAgentCycleV1, ResearchAgentOpenWorkV1
from trading_agent.research_agent_runtime_models import ResearchAgentTickResult, RuntimeCycleOutcome
from trading_agent.research_agent_wake_policy import ActorWakeState


def prior_failures(states: tuple[ActorWakeState, ...], family: AgentFamilyId, market_id: str) -> int:
    return next(
        (
            state.consecutive_failures
            for state in states
            if state.agent_family_id == family and (family != "day_trading" or state.market_id == market_id)
        ),
        0,
    )


def tick_result(outcome: RuntimeCycleOutcome) -> ResearchAgentTickResult:
    return ResearchAgentTickResult(
        status=outcome.result.status.value,
        agent_family_id=outcome.cycle.agent_family_id,
        cycle_id=outcome.cycle.cycle_id,
        model_calls=outcome.model_calls,
        recovered_cycles=outcome.recovered_cycles,
    )


def work_matches_cycle(item: ResearchAgentOpenWorkV1, cycle: ResearchAgentCycleV1) -> bool:
    if cycle.agent_family_id != "day_trading":
        return True
    if item.work_id == "actor-state.day_trading":
        return cycle.market_id == "us_equities"
    return item.work_id == f"actor-state.day_trading.{cycle.market_id}"


__all__ = ("prior_failures", "tick_result", "work_matches_cycle")
