from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

from pydantic import TypeAdapter

from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    ExperimentLedgerStore,
    ExperimentLedgerWriterLeaseUnavailableError,
)
from trading_agent.strategy_research_ledger import AgentResearchStateEvent, StrategyResearchLedgerError
from trading_agent.strategy_research_runtime_models import (
    SlotState,
    StrategyResearchAgentSlot,
    StrategyResearchRuntimeBusyError,
    StrategyResearchWork,
)
from trading_agent.strategy_research_types import ResearchAgentId


@dataclass(frozen=True, slots=True)
class AgentStateTransition:
    agent_id: ResearchAgentId
    occurred_at: dt.datetime
    state: SlotState
    reason: str
    cursor: str | None = None
    work: StrategyResearchWork | None = None
    next_due_at: dt.datetime | None = None
    next_maturity_at: dt.datetime | None = None
    lease_until: dt.datetime | None = None
    retry_count: int = 0
    next_retry_at: dt.datetime | None = None


class StrategyResearchStateJournal:
    __slots__ = ("_store",)

    def __init__(self, store: ExperimentLedgerStore) -> None:
        self._store = store

    def latest(self, agent_id: ResearchAgentId) -> AgentResearchStateEvent | None:
        events = ExperimentLedgerReader(self._store.path).strategy_research_agent_state(agent_id)
        return None if not events else events[-1]

    def append(
        self,
        previous: AgentResearchStateEvent,
        transition: AgentStateTransition,
    ) -> AgentResearchStateEvent:
        candidate = self.event(previous, transition)
        return self.write(candidate)

    def event(
        self,
        previous: AgentResearchStateEvent | None,
        transition: AgentStateTransition,
    ) -> AgentResearchStateEvent:
        sequence = 1 if previous is None else previous.sequence + 1
        work = transition.work
        checkpoint = (
            previous.checkpoint_sha256
            if work is None and previous is not None
            else None if work is None else work.content_sha256
        )
        cursor = transition.cursor
        event_id = hashlib.sha256(
            f"{transition.agent_id.value}:{sequence}:{transition.state}:{cursor}:{checkpoint}".encode()
        ).hexdigest()
        return AgentResearchStateEvent(
            event_id=event_id,
            agent_id=transition.agent_id,
            sequence=sequence,
            last_event_id="cursor:origin" if cursor is None else cursor,
            last_available_at=(
                previous.last_available_at
                if work is None and previous is not None
                else transition.occurred_at if work is None else work.available_at
            ),
            version=1,
            hypothesis_id=(
                previous.hypothesis_id
                if work is None and previous is not None
                else None if work is None else work.draft.hypothesis_id
            ),
            attempt_id=(
                previous.attempt_id
                if work is None and previous is not None
                else None if work is None else f"work-{work.content_sha256}"
            ),
            state=transition.state,
            lease_until=transition.lease_until,
            checkpoint_sha256=checkpoint,
            retry_count=transition.retry_count,
            next_retry_at=transition.next_retry_at,
            next_due_at=transition.next_due_at,
            next_maturity_at=transition.next_maturity_at,
            reason=transition.reason,
        )

    def write(self, event: AgentResearchStateEvent) -> AgentResearchStateEvent:
        try:
            with self._store.writer() as writer:
                _ = writer.append_strategy_research_agent_state(event)
        except ExperimentLedgerWriterLeaseUnavailableError:
            raise StrategyResearchRuntimeBusyError from None
        return event

    def feedback_exists(self, work: StrategyResearchWork) -> bool:
        return any(
            item.hypothesis_id == work.draft.hypothesis_id
            for item in ExperimentLedgerReader(self._store.path).strategy_research_feedback(work.draft.agent_id)
        )


def same_projection(left: AgentResearchStateEvent, right: AgentResearchStateEvent) -> bool:
    return (
        left.state,
        left.last_event_id,
        left.next_due_at,
        left.next_maturity_at,
        left.next_retry_at,
    ) == (
        right.state,
        right.last_event_id,
        right.next_due_at,
        right.next_maturity_at,
        right.next_retry_at,
    )


def slot_from_event(event: AgentResearchStateEvent | None) -> StrategyResearchAgentSlot:
    if event is None:
        raise StrategyResearchLedgerError("agent_state_missing")
    return StrategyResearchAgentSlot(
        agent_id=event.agent_id,
        state=TypeAdapter(SlotState).validate_python(event.state),
        evidence_cursor=None if event.last_event_id == "cursor:origin" else event.last_event_id,
        next_due_at=event.next_due_at,
        next_maturity_at=event.next_maturity_at,
        hypothesis_id=event.hypothesis_id,
        attempt_id=event.attempt_id,
        checkpoint_sha256=event.checkpoint_sha256,
        retry_count=event.retry_count,
    )


__all__ = ("AgentStateTransition", "StrategyResearchStateJournal", "same_projection", "slot_from_event")
