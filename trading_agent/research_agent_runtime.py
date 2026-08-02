from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, Self, assert_never, final

import anyio
from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.research_agent_actions import (
    InvalidResearchAgentActionError,
    ResearchAgentActionExecutor,
)
from trading_agent.research_agent_cycle_models import (
    CycleId,
    ResearchAgentCycleV1,
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkState,
    ResearchAgentOpenWorkV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
)
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore, StoredResearchAgentEvidence
from trading_agent.research_agent_decision import (
    InvalidResearchAgentDecisionError,
    ResearchAgentDecisionClient,
    ResearchAgentDecisionRequest,
)
from trading_agent.research_agent_runtime_lease import (
    ResearchAgentRuntimeLeaseUnavailableError,
    research_agent_runtime_lease,
)
from trading_agent.research_agent_runtime_support import (
    ActorStateContext,
    RuntimeFailureContext,
    actor_state_work,
    actor_wake_states,
    normalize_failure_backoff,
    retry_evidence,
    runtime_failure_result,
    scheduled_evidence,
    source_failure_evidence,
)
from trading_agent.research_agent_sources import (
    ResearchAgentSourceCollectionBatch,
    ResearchAgentSourcePaths,
    collect_research_agent_evidence_isolated,
)
from trading_agent.research_agent_systematic import InvalidSystematicResearchActionError
from trading_agent.research_agent_wake_policy import ACTOR_WAKE_POLICIES, ActorWakeState, runnable_actors


class ResearchAgentEvidenceCollector(Protocol):
    def collect(self, now: dt.datetime) -> ResearchAgentSourceCollectionBatch: ...


@dataclass(frozen=True, slots=True)
class ConfiguredResearchAgentEvidenceCollector:
    paths: ResearchAgentSourcePaths

    def collect(self, now: dt.datetime) -> ResearchAgentSourceCollectionBatch:
        return collect_research_agent_evidence_isolated(self.paths, now=now)


@dataclass(frozen=True, slots=True)
class ResearchAgentRuntimeServices:
    store: ResearchAgentCycleStore
    collector: ResearchAgentEvidenceCollector
    decisions: ResearchAgentDecisionClient
    actions: ResearchAgentActionExecutor


@dataclass(frozen=True, slots=True)
class EvidenceResolution:
    family: AgentFamilyId
    stored: StoredResearchAgentEvidence | None
    open_work: ResearchAgentOpenWorkV1 | None
    now: dt.datetime


@dataclass(frozen=True, slots=True)
class RuntimeCycleOutcome:
    cycle: ResearchAgentCycleV1
    evidence: ResearchAgentEvidenceV1
    result: ResearchAgentResultV1
    prior_failures: int
    model_calls: Literal[0, 1]
    recovered_cycles: int


class InvalidResearchAgentRuntimeError(RuntimeError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ResearchAgentTickResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: Literal["idle", "completed", "failed", "blocked", "no_action"]
    agent_family_id: AgentFamilyId | None
    cycle_id: CycleId | None = Field(pattern=r"^[a-f0-9]{64}$")
    model_calls: Literal[0, 1]
    recovered_cycles: int = Field(ge=0)

    @model_validator(mode="after")
    def require_idle_identity(self) -> Self:
        idle = self.status == "idle"
        if idle != (self.agent_family_id is None and self.cycle_id is None and self.model_calls == 0):
            raise InvalidResearchAgentRuntimeError(reason="tick_result_identity_invalid")
        return self


@final
class ResearchAgentRuntime:
    __slots__ = ("_actions", "_collector", "_decisions", "store")

    store: ResearchAgentCycleStore

    def __init__(self, services: ResearchAgentRuntimeServices) -> None:
        self.store = services.store
        self._collector = services.collector
        self._decisions = services.decisions
        self._actions = services.actions

    def close(self) -> None:
        self.store.close()

    def ingest(self, evidence: tuple[ResearchAgentEvidenceV1, ...]) -> int:
        return sum(self.store.append_evidence(item) for item in evidence)

    def tick(self, now: dt.datetime) -> ResearchAgentTickResult:
        recovered = self.store.recover_interrupted(now)
        batch = self._collector.collect(now)
        failures = tuple(source_failure_evidence(failure) for failure in batch.failures)
        self.ingest((*batch.evidence, *failures))
        pending = tuple(
            stored for family in PRIMARY_AGENT_FAMILIES for stored in self.store.runnable_evidence(family, now)
        )
        work = tuple(item for family in PRIMARY_AGENT_FAMILIES for item in self.store.open_work(family))
        states = actor_wake_states(self.store.latest_cycles(), work)
        selected = runnable_actors(pending, work, now=now, states=states)
        if not selected:
            return ResearchAgentTickResult(
                status="idle",
                agent_family_id=None,
                cycle_id=None,
                model_calls=0,
                recovered_cycles=len(recovered),
            )
        actor = selected[0]
        stored = self._resolve_evidence(EvidenceResolution(actor.agent_family_id, actor.evidence, actor.open_work, now))
        cycle = self.store.start_cycle(stored, now)
        prior_failures = _prior_failures(states, cycle.agent_family_id)
        if stored.evidence.source_key.startswith("source_failure."):
            result = runtime_failure_result(
                RuntimeFailureContext(
                    cycle,
                    stored.evidence,
                    stored.evidence.source_key.removeprefix("source_failure."),
                    now,
                    prior_failures,
                )
            )
            outcome = RuntimeCycleOutcome(cycle, stored.evidence, result, prior_failures, 0, len(recovered))
            self._persist(outcome)
            return _tick_result(outcome)
        request = ResearchAgentDecisionRequest(
            cycle_id=cycle.cycle_id,
            agent_family_id=cycle.agent_family_id,
            evidence=(stored.evidence,),
            open_work=tuple(
                item
                for item in work
                if item.agent_family_id == cycle.agent_family_id and item.state is ResearchAgentOpenWorkState.OPEN
            ),
            requested_at=now,
            max_runtime_seconds=120.0,
            max_model_calls=1,
        )
        try:
            decision = self._decisions.decide(request)
        except InvalidResearchAgentDecisionError as error:
            result = runtime_failure_result(
                RuntimeFailureContext(cycle, stored.evidence, error.reason, now, prior_failures)
            )
            outcome = RuntimeCycleOutcome(cycle, stored.evidence, result, prior_failures, 1, len(recovered))
            self._persist(outcome)
            return _tick_result(outcome)
        try:
            result = self._actions.execute(cycle, decision)
        except (InvalidResearchAgentActionError, InvalidSystematicResearchActionError) as error:
            result = runtime_failure_result(
                RuntimeFailureContext(cycle, stored.evidence, error.reason, now, prior_failures)
            )
        outcome = RuntimeCycleOutcome(cycle, stored.evidence, result, prior_failures, 1, len(recovered))
        self._persist(outcome)
        return _tick_result(outcome)

    def _resolve_evidence(self, selection: EvidenceResolution) -> StoredResearchAgentEvidence:
        family = selection.family
        stored = selection.stored
        if stored is not None:
            return stored
        if selection.open_work is not None:
            evidence = retry_evidence(selection.open_work, selection.now)
        else:
            policy = next(item for item in ACTOR_WAKE_POLICIES if item.family_id == family)
            if policy.scheduled_interval is None:
                raise InvalidResearchAgentRuntimeError(reason="scheduled_policy_interval_missing")
            evidence = scheduled_evidence(
                family,
                selection.now,
                int(policy.scheduled_interval.total_seconds() // 60),
            )
        _ = self.store.append_evidence(evidence)
        candidates = self.store.runnable_evidence(family, selection.now)
        return next(item for item in reversed(candidates) if item.evidence.evidence_id == evidence.evidence_id)

    def _persist(self, outcome: RuntimeCycleOutcome) -> None:
        normalized = normalize_failure_backoff(outcome.result, outcome.prior_failures)
        match normalized.status:
            case ResearchAgentResultStatus.COMPLETED | ResearchAgentResultStatus.NO_ACTION:
                self.store.finish_cycle(outcome.cycle, normalized)
            case ResearchAgentResultStatus.FAILED | ResearchAgentResultStatus.BLOCKED:
                self.store.fail_cycle(outcome.cycle, normalized)
            case unreachable:
                assert_never(unreachable)
        state = ActorStateContext(
            outcome.cycle,
            outcome.evidence,
            normalized,
            outcome.prior_failures,
        )
        self.store.upsert_open_work(actor_state_work(state))


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


def _prior_failures(states: tuple[ActorWakeState, ...], family: AgentFamilyId) -> int:
    return next(state.consecutive_failures for state in states if state.agent_family_id == family)


def _tick_result(outcome: RuntimeCycleOutcome) -> ResearchAgentTickResult:
    return ResearchAgentTickResult(
        status=outcome.result.status.value,
        agent_family_id=outcome.cycle.agent_family_id,
        cycle_id=outcome.cycle.cycle_id,
        model_calls=outcome.model_calls,
        recovered_cycles=outcome.recovered_cycles,
    )


__all__ = (
    "ConfiguredResearchAgentEvidenceCollector",
    "InvalidResearchAgentRuntimeError",
    "ResearchAgentEvidenceCollector",
    "ResearchAgentRuntime",
    "ResearchAgentRuntimeLeaseUnavailableError",
    "ResearchAgentRuntimeServices",
    "ResearchAgentTickResult",
    "research_agent_runtime_lease",
    "run_research_agent_forever",
)
