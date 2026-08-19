from __future__ import annotations

import datetime as dt

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.heavy_empirical_lease import HeavyEmpiricalLeaseError, heavy_empirical_lease
from trading_agent.strategy_research_catalog import STRATEGY_RESEARCH_CATALOG, StrategyResearchIdentity
from trading_agent.strategy_research_feedback_runtime import feedback_admission, owner_feedback
from trading_agent.strategy_research_ledger import AgentResearchStateEvent, StrategyResearchLedgerError
from trading_agent.strategy_research_policy import OwnerFeedbackDecision
from trading_agent.strategy_research_resource_guard import (
    ScienceKernelRssLimitError,
    require_science_kernel_rss_below_limit,
)
from trading_agent.strategy_research_runtime_models import (
    InvalidStrategyResearchWorkSourceError,
    SlotState,
    StrategyResearchAgentSlot,
    StrategyResearchCycleRunner,
    StrategyResearchRuntimeBusyError,
    StrategyResearchRuntimeStatus,
    StrategyResearchWork,
    StrategyResearchWorkSource,
)
from trading_agent.strategy_research_runtime_state import (
    AgentStateTransition,
    StrategyResearchStateJournal,
    same_projection,
    slot_from_event,
)
from trading_agent.strategy_research_types import ResearchAgentId


class StrategyResearchRuntime:
    __slots__ = ("_journal", "_runner", "_source", "_store")

    def __init__(
        self,
        store: ExperimentLedgerStore,
        source: StrategyResearchWorkSource,
        runner: StrategyResearchCycleRunner,
    ) -> None:
        self._store = store
        self._journal = StrategyResearchStateJournal(store)
        self._source = source
        self._runner = runner

    def tick(self, now: dt.datetime) -> StrategyResearchRuntimeStatus:
        try:
            with heavy_empirical_lease(self._store.path):
                require_science_kernel_rss_below_limit()
                return self._tick_with_heavy_lease(now)
        except HeavyEmpiricalLeaseError:
            raise StrategyResearchRuntimeBusyError("heavy_empirical_lease_busy") from None
        except ScienceKernelRssLimitError:
            raise StrategyResearchRuntimeBusyError("science_kernel_rss_limit_reached") from None

    def _tick_with_heavy_lease(self, now: dt.datetime) -> StrategyResearchRuntimeStatus:
        self._recover_interrupted(now)
        candidates: list[tuple[dt.datetime, int, StrategyResearchWork, AgentResearchStateEvent]] = []
        for order, identity in enumerate(STRATEGY_RESEARCH_CATALOG):
            event, work = self._evaluate(identity, now)
            if work is not None and self._ready(event, now):
                candidates.append((event.next_due_at or now, order, work, event))
        if not candidates:
            return self._status(now, None)
        _, _, work, event = min(candidates, key=lambda item: (item[0], item[1]))
        started = self._journal.append(
            event,
            AgentStateTransition(
                agent_id=event.agent_id,
                occurred_at=now,
                state="started",
                reason="science_cycle_started",
                cursor=event.last_event_id,
                work=work,
                next_due_at=event.next_due_at,
                next_maturity_at=event.next_maturity_at,
                lease_until=now + dt.timedelta(minutes=5),
                retry_count=event.retry_count,
            ),
        )
        if self._journal.feedback_exists(work):
            _ = self._complete(started, work, now, "recovered_completed_feedback")
            return self._status(now, None)
        try:
            result = self._runner.run(work)
        except StrategyResearchLedgerError as error:
            _ = self._journal.append(
                started,
                AgentStateTransition(
                    agent_id=started.agent_id,
                    occurred_at=now,
                    state="recovery_pending",
                    reason=error.reason,
                    cursor=started.last_event_id,
                    work=work,
                    next_due_at=started.next_due_at,
                    next_maturity_at=started.next_maturity_at,
                    retry_count=started.retry_count + 1,
                    next_retry_at=now + dt.timedelta(seconds=30),
                ),
            )
        else:
            require_science_kernel_rss_below_limit()
            _ = self._complete(started, work, now, f"science_cycle_completed:{result.feedback_result_id}")
        return self._status(now, work.draft.agent_id)

    def owner_feedback(self, owner_agent_id: ResearchAgentId) -> OwnerFeedbackDecision | None:
        return owner_feedback(self._store, owner_agent_id)

    def _evaluate(
        self,
        identity: StrategyResearchIdentity,
        now: dt.datetime,
    ) -> tuple[AgentResearchStateEvent, StrategyResearchWork | None]:
        previous = self._journal.latest(identity.agent_id)
        cursor = None if previous is None or previous.last_event_id == "cursor:origin" else previous.last_event_id
        try:
            work = self._source.next_work(identity.agent_id, cursor)
        except InvalidStrategyResearchWorkSourceError as error:
            failed = self._journal.event(
                previous,
                AgentStateTransition(
                    agent_id=identity.agent_id,
                    occurred_at=now,
                    state="recovery_pending",
                    reason=error.reason,
                    cursor=cursor,
                    next_due_at=None if previous is None else previous.next_due_at,
                    next_maturity_at=None if previous is None else previous.next_maturity_at,
                    retry_count=1 if previous is None else previous.retry_count + 1,
                    next_retry_at=now + dt.timedelta(seconds=30),
                ),
            )
            return self._journal.write(failed), None
        admission = feedback_admission(self._store, identity.agent_id, work)
        if work is not None and admission is not None and not admission.allowed:
            waiting = self._journal.event(
                previous,
                AgentStateTransition(
                    agent_id=identity.agent_id,
                    occurred_at=now,
                    state="waiting_feedback",
                    reason=admission.reason,
                    cursor=cursor,
                    work=work,
                    next_due_at=admission.not_before,
                    next_maturity_at=work.maturity_at,
                    retry_count=0 if previous is None else previous.retry_count,
                ),
            )
            if previous is not None and same_projection(previous, waiting):
                return previous, None
            return self._journal.write(waiting), None
        if work is None:
            if previous is not None and previous.state in {"completed", "forward_shadow", "paper_candidate"}:
                return previous, None
            state: SlotState = "waiting_evidence"
            due = previous.next_due_at if previous is not None else None
            maturity = previous.next_maturity_at if previous is not None else None
        else:
            due = work.available_at + dt.timedelta(minutes=identity.cadence.delay_minutes)
            if admission is not None and admission.not_before is not None:
                due = max(due, admission.not_before)
            maturity = work.maturity_at
            if work.experiment is None:
                state = "waiting_maturity" if maturity > now else "waiting_evidence"
            elif previous is not None and previous.next_retry_at is not None and previous.next_retry_at > now:
                state = "recovery_pending"
            else:
                state = "waiting_due" if due > now else "waiting_maturity" if maturity > now else "due"
        candidate = self._journal.event(
            previous,
            AgentStateTransition(
                agent_id=identity.agent_id,
                occurred_at=now,
                state=state,
                reason=f"cadence_{state}",
                cursor=cursor,
                work=work,
                next_due_at=due,
                next_maturity_at=maturity,
                retry_count=0 if previous is None else previous.retry_count,
                next_retry_at=None if previous is None else previous.next_retry_at,
            ),
        )
        if previous is not None and same_projection(previous, candidate):
            return previous, work
        return self._journal.write(candidate), work

    def _recover_interrupted(self, now: dt.datetime) -> None:
        for identity in STRATEGY_RESEARCH_CATALOG:
            previous = self._journal.latest(identity.agent_id)
            if previous is not None and previous.state == "started":
                _ = self._journal.append(
                    previous,
                    AgentStateTransition(
                        agent_id=previous.agent_id,
                        occurred_at=now,
                        state="recovery_pending",
                        reason="interrupted_started_work",
                        cursor=previous.last_event_id,
                        next_due_at=previous.next_due_at,
                        next_maturity_at=previous.next_maturity_at,
                        retry_count=previous.retry_count + 1,
                        next_retry_at=now,
                    ),
                )

    def _complete(
        self,
        previous: AgentResearchStateEvent,
        work: StrategyResearchWork,
        now: dt.datetime,
        reason: str,
    ) -> AgentResearchStateEvent:
        return self._journal.append(
            previous,
            AgentStateTransition(
                agent_id=previous.agent_id,
                occurred_at=now,
                state="completed",
                reason=reason,
                cursor=work.evidence_event_id,
                work=work,
                next_due_at=previous.next_due_at,
                next_maturity_at=previous.next_maturity_at,
                retry_count=previous.retry_count,
            ),
        )

    def _status(
        self,
        now: dt.datetime,
        heavy_agent_id: ResearchAgentId | None,
    ) -> StrategyResearchRuntimeStatus:
        slots = tuple(slot_from_event(self._journal.latest(row.agent_id)) for row in STRATEGY_RESEARCH_CATALOG)
        return StrategyResearchRuntimeStatus(
            slots=slots,
            heavy_cycles_started=0 if heavy_agent_id is None else 1,
            heavy_agent_id=heavy_agent_id,
            observed_at=now,
        )

    @staticmethod
    def _ready(event: AgentResearchStateEvent, now: dt.datetime) -> bool:
        return (
            event.state in {"due", "recovery_pending"}
            and (event.next_due_at is None or event.next_due_at <= now)
            and (event.next_maturity_at is None or event.next_maturity_at <= now)
            and (event.next_retry_at is None or event.next_retry_at <= now)
        )


__all__ = (
    "StrategyResearchAgentSlot",
    "StrategyResearchCycleRunner",
    "StrategyResearchRuntime",
    "StrategyResearchRuntimeStatus",
    "StrategyResearchWork",
    "StrategyResearchWorkSource",
)
