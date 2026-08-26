from __future__ import annotations

from typing import assert_never

from trading_agent.research_agent_cycle_models import (
    ResearchAgentOpenWorkV1,
    ResearchAgentResultStatus,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_cycle_terminal_store import CycleTerminalization
from trading_agent.research_agent_runtime_models import RuntimeCycleOutcome
from trading_agent.research_agent_runtime_support import (
    ActorStateContext,
    actor_state_work,
    normalize_failure_backoff,
)


def persist_cycle_outcome(
    store: ResearchAgentCycleStore,
    outcome: RuntimeCycleOutcome,
    legacy_work: ResearchAgentOpenWorkV1 | None = None,
) -> None:
    normalized = (
        outcome.result
        if outcome.supervisor_owned
        else normalize_failure_backoff(outcome.result, outcome.prior_failures)
    )
    if legacy_work is not None:
        store.terminalize_supervisor_cycle(CycleTerminalization(outcome.cycle, normalized, legacy_work))
    else:
        match normalized.status:
            case ResearchAgentResultStatus.COMPLETED | ResearchAgentResultStatus.NO_ACTION:
                store.finish_cycle(outcome.cycle, normalized)
            case ResearchAgentResultStatus.FAILED | ResearchAgentResultStatus.BLOCKED:
                store.fail_cycle(outcome.cycle, normalized)
            case unreachable:
                assert_never(unreachable)
    if not outcome.supervisor_owned:
        store.upsert_open_work(
            actor_state_work(
                ActorStateContext(
                    outcome.cycle,
                    outcome.evidence,
                    normalized,
                    outcome.prior_failures,
                )
            )
        )


__all__ = ("persist_cycle_outcome",)
