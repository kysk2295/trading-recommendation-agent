from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Literal, final

import anyio

from trading_agent.autonomous_supervisor_cycle_adapter import ResearchCycleEvidenceResolver
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.research_agent_actions import (
    InvalidResearchAgentActionError,
    ResearchAgentActionContext,
)
from trading_agent.research_agent_configured_collector import ConfiguredResearchAgentEvidenceCollector
from trading_agent.research_agent_cycle_models import (
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkState,
    ResearchAgentOpenWorkV1,
)
from trading_agent.research_agent_cycle_persistence import persist_cycle_outcome
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_decision import (
    InvalidResearchAgentDecisionError,
    ResearchAgentDecisionRequest,
)
from trading_agent.research_agent_runtime_lease import (
    ResearchAgentRuntimeLeaseUnavailableError,
    research_agent_runtime_lease,
)
from trading_agent.research_agent_runtime_models import (
    InvalidResearchAgentRuntimeError,
    PersistentResearchSupervisor,
    ResearchAgentBoundedCycleResult,
    ResearchAgentEvidenceCollector,
    ResearchAgentRuntimeServices,
    ResearchAgentTickResult,
    RuntimeCycleOutcome,
)
from trading_agent.research_agent_runtime_selection import prior_failures, tick_result, work_matches_cycle
from trading_agent.research_agent_runtime_support import (
    RuntimeFailureContext,
    actor_wake_states,
    primary_admission_no_action,
    runtime_failure_result,
    source_failure_evidence,
)
from trading_agent.research_agent_systematic import InvalidSystematicResearchActionError
from trading_agent.research_agent_wake_policy import runnable_actors


@final
class ResearchAgentRuntime:
    __slots__ = ("_actions", "_collector", "_decisions", "_supervisor_runtime", "store")

    store: ResearchAgentCycleStore

    def __init__(self, services: ResearchAgentRuntimeServices) -> None:
        self.store = services.store
        self._collector = services.collector
        self._decisions = services.decisions
        self._actions = services.actions
        self._supervisor_runtime = services.supervisor_runtime

    def close(self) -> None:
        try:
            self.store.close()
        finally:
            if isinstance(self._supervisor_runtime, PersistentResearchSupervisor):
                self._supervisor_runtime.close()

    @property
    def supervisor_enabled(self) -> bool:
        return self._supervisor_runtime is not None

    def ingest(self, evidence: tuple[ResearchAgentEvidenceV1, ...]) -> int:
        return sum(self.store.append_evidence(item) for item in evidence)

    def tick(self, now: dt.datetime) -> ResearchAgentTickResult:
        return self._tick(now, only_family=None, apply_debounce=True)

    def cycle(self, now: dt.datetime) -> ResearchAgentBoundedCycleResult:
        outcomes: list[ResearchAgentTickResult] = []
        for family in PRIMARY_AGENT_FAMILIES:
            outcome = self._tick(now, only_family=family, apply_debounce=False)
            if outcome.status != "idle":
                outcomes.append(outcome)
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
            outcomes=tuple(outcomes),
            model_calls=sum(item.model_calls for item in outcomes),
            recovered_cycles=sum(item.recovered_cycles for item in outcomes),
        )

    def _tick(
        self,
        now: dt.datetime,
        *,
        only_family: AgentFamilyId | None,
        apply_debounce: bool,
    ) -> ResearchAgentTickResult:
        recovered = self.store.recover_interrupted(now)
        batch = self._collector.collect(now)
        failures = tuple(source_failure_evidence(failure) for failure in batch.failures)
        self.ingest((*batch.evidence, *failures))
        pending = tuple(
            stored for family in PRIMARY_AGENT_FAMILIES for stored in self.store.runnable_evidence(family, now)
        )
        work = tuple(item for family in PRIMARY_AGENT_FAMILIES for item in self.store.open_work(family))
        states = actor_wake_states(self.store.latest_cycles(), work)
        selected = runnable_actors(
            pending,
            work,
            now=now,
            states=states,
            apply_debounce=apply_debounce,
        )
        if only_family is not None:
            selected = tuple(actor for actor in selected if actor.agent_family_id == only_family)
        if not selected:
            return ResearchAgentTickResult(
                status="idle",
                agent_family_id=None,
                cycle_id=None,
                model_calls=0,
                recovered_cycles=len(recovered),
            )
        actor = selected[0]
        resolver = ResearchCycleEvidenceResolver(self.store, now, self._supervisor_runtime is not None)
        resolved = resolver.resolve(actor)
        stored = resolved.stored
        cycle = self.store.start_cycle(
            stored,
            now,
            preserve_authority=resolved.legacy_work is not None,
        )
        failures_before = prior_failures(states, cycle.agent_family_id, cycle.market_id)
        no_action = primary_admission_no_action(cycle, stored.evidence, now)
        if no_action is not None:
            outcome = RuntimeCycleOutcome(cycle, stored.evidence, no_action, failures_before, 0, len(recovered))
            self._persist(outcome)
            return tick_result(outcome)
        if stored.evidence.source_key.startswith("source_failure."):
            result = runtime_failure_result(
                RuntimeFailureContext(
                    cycle,
                    stored.evidence,
                    stored.evidence.source_key.removeprefix("source_failure."),
                    now,
                    failures_before,
                )
            )
            outcome = RuntimeCycleOutcome(cycle, stored.evidence, result, failures_before, 0, len(recovered))
            self._persist(outcome)
            return tick_result(outcome)
        if self._supervisor_runtime is not None:
            supervisor_result = self._supervisor_runtime.tick(stored.evidence, now)
            result = self._supervisor_runtime.project_tick(cycle, supervisor_result, now)
            outcome = RuntimeCycleOutcome(
                cycle,
                stored.evidence,
                result,
                failures_before,
                supervisor_result.model_calls,
                len(recovered),
                supervisor_owned=True,
            )
            legacy_work = (
                None
                if resolved.legacy_work is None
                else resolver.terminal_legacy_work(resolved.legacy_work)
            )
            self._persist(outcome, legacy_work)
            return tick_result(outcome)
        request = ResearchAgentDecisionRequest(
            cycle_id=cycle.cycle_id,
            agent_family_id=cycle.agent_family_id,
            evidence=(stored.evidence,),
            open_work=tuple(
                item
                for item in work
                if item.agent_family_id == cycle.agent_family_id
                and item.state is ResearchAgentOpenWorkState.OPEN
                and work_matches_cycle(item, cycle)
            ),
            requested_at=now,
            max_runtime_seconds=120.0,
            max_model_calls=1,
        )
        try:
            decision = self._decisions.decide(request)
        except InvalidResearchAgentDecisionError as error:
            result = runtime_failure_result(
                RuntimeFailureContext(cycle, stored.evidence, error.reason, now, failures_before)
            )
            outcome = RuntimeCycleOutcome(cycle, stored.evidence, result, failures_before, 1, len(recovered))
            self._persist(outcome)
            return tick_result(outcome)
        context = ResearchAgentActionContext(
            cycle=cycle,
            evidence=request.evidence,
            open_work=request.open_work,
            decision=decision,
            observed_at=now,
        )
        try:
            result = self._actions.execute(context)
        except (InvalidResearchAgentActionError, InvalidSystematicResearchActionError) as error:
            result = runtime_failure_result(
                RuntimeFailureContext(cycle, stored.evidence, error.reason, now, failures_before)
            )
        if result.decision_kind is None:
            result = result.model_copy(update={"decision_kind": decision.primary_decision})
        outcome = RuntimeCycleOutcome(cycle, stored.evidence, result, failures_before, 1, len(recovered))
        self._persist(outcome)
        return tick_result(outcome)

    def _persist(
        self,
        outcome: RuntimeCycleOutcome,
        legacy_work: ResearchAgentOpenWorkV1 | None = None,
    ) -> None:
        persist_cycle_outcome(self.store, outcome, legacy_work)


async def run_research_agent_forever(
    runtime: ResearchAgentRuntime,
    clock: Callable[[], dt.datetime],
    tick_seconds: float = 30.0,
) -> None:
    if tick_seconds <= 0:
        raise InvalidResearchAgentRuntimeError(reason="tick_seconds_invalid")
    while True:
        _ = runtime.tick(clock())
        await anyio.sleep(tick_seconds)


__all__ = (
    "ConfiguredResearchAgentEvidenceCollector",
    "InvalidResearchAgentRuntimeError",
    "PersistentResearchSupervisor",
    "ResearchAgentBoundedCycleResult",
    "ResearchAgentEvidenceCollector",
    "ResearchAgentRuntime",
    "ResearchAgentRuntimeLeaseUnavailableError",
    "ResearchAgentRuntimeServices",
    "ResearchAgentTickResult",
    "research_agent_runtime_lease",
    "run_research_agent_forever",
)
