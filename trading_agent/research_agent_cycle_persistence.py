from __future__ import annotations

from typing import assert_never

from trading_agent.research_agent_cycle_models import (
    ResearchAgentOpenWorkState,
    ResearchAgentOpenWorkV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
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
    supervisor_work = supervisor_audit_work(normalized) if outcome.supervisor_owned else None
    terminal_work = legacy_work or supervisor_work
    if terminal_work is not None:
        store.terminalize_supervisor_cycle(CycleTerminalization(outcome.cycle, normalized, terminal_work))
    else:
        match normalized.status:
            case ResearchAgentResultStatus.COMPLETED | ResearchAgentResultStatus.NO_ACTION:
                store.finish_cycle(outcome.cycle, normalized)
            case ResearchAgentResultStatus.FAILED | ResearchAgentResultStatus.BLOCKED:
                store.fail_cycle(outcome.cycle, normalized)
            case unreachable:
                assert_never(unreachable)
    if outcome.supervisor_owned:
        if legacy_work is not None and supervisor_work is not None:
            store.upsert_open_work(supervisor_work)
        return
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


def supervisor_audit_work(result: ResearchAgentResultV1) -> ResearchAgentOpenWorkV1:
    return ResearchAgentOpenWorkV1(
        work_id=result.open_work_ref or f"supervisor-audit.{result.cycle_id}",
        cycle_id=result.cycle_id,
        agent_family_id=result.agent_family_id,
        state=ResearchAgentOpenWorkState.TERMINAL,
        evidence_refs=result.evidence_refs,
        next_wake_at=None,
        updated_at=result.occurred_at,
    )


__all__ = ("persist_cycle_outcome", "supervisor_audit_work")
