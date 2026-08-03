from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final, Literal, assert_never

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_cycle_models import (
    EvidenceId,
    ResearchAgentOpenWorkState,
    ResearchAgentOpenWorkV1,
    ResearchAgentTriggerKind,
)
from trading_agent.research_agent_cycle_store import StoredResearchAgentEvidence


@dataclass(frozen=True, slots=True)
class ActorWakePolicy:
    family_id: AgentFamilyId
    debounce: dt.timedelta
    scheduled_interval: dt.timedelta | None
    priority: int
    max_model_calls_per_cycle: Literal[1] = 1


@dataclass(frozen=True, slots=True)
class ActorWakeState:
    agent_family_id: AgentFamilyId
    last_terminal_at: dt.datetime | None
    cooldown_until: dt.datetime | None
    consecutive_failures: int
    last_failed_evidence_id: EvidenceId | None


@unique
class ResearchActorWakeReason(StrEnum):
    OPEN_WORK = "open_work"
    CURRENT_SESSION = "current_session"
    REVIEWER_FEEDBACK = "reviewer_feedback"
    SOURCE_EVENT = "source_event"
    SCHEDULED = "scheduled"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class RunnableResearchActor:
    agent_family_id: AgentFamilyId
    reason: ResearchActorWakeReason
    evidence: StoredResearchAgentEvidence | None
    open_work: ResearchAgentOpenWorkV1 | None
    priority: int


@dataclass(frozen=True, slots=True)
class ActorWakeEvaluation:
    policy: ActorWakePolicy
    state: ActorWakeState | None
    evidence: StoredResearchAgentEvidence | None
    open_work: ResearchAgentOpenWorkV1 | None
    now: dt.datetime


ACTOR_WAKE_POLICIES: Final[tuple[ActorWakePolicy, ...]] = (
    ActorWakePolicy("opportunity_manager", dt.timedelta(minutes=2), None, 30),
    ActorWakePolicy("market_context", dt.timedelta(0), dt.timedelta(minutes=30), 40),
    ActorWakePolicy("day_trading", dt.timedelta(0), None, 10),
    ActorWakePolicy("swing_trading", dt.timedelta(0), None, 40),
    ActorWakePolicy("systematic_quant", dt.timedelta(0), None, 20),
    ActorWakePolicy("derivatives_research", dt.timedelta(0), dt.timedelta(minutes=15), 40),
)

_OPEN_WORK_PRIORITY: Final = 5
_REVIEWER_PRIORITY: Final = 15
_SCHEDULED_PRIORITY: Final = 50
_RETRY_PRIORITY: Final = 60


def retry_delay(consecutive_failures: int) -> dt.timedelta | None:
    if consecutive_failures <= 0:
        return dt.timedelta(0)
    if consecutive_failures == 1:
        return dt.timedelta(minutes=15)
    if consecutive_failures == 2:
        return dt.timedelta(hours=1)
    if consecutive_failures == 3:
        return dt.timedelta(hours=4)
    return None


def runnable_actors(
    evidence: tuple[StoredResearchAgentEvidence, ...],
    open_work: tuple[ResearchAgentOpenWorkV1, ...],
    *,
    now: dt.datetime,
    states: tuple[ActorWakeState, ...] = (),
    apply_debounce: bool = True,
) -> tuple[RunnableResearchActor, ...]:
    policies = {policy.family_id: policy for policy in ACTOR_WAKE_POLICIES}
    state_by_family = {state.agent_family_id: state for state in states}
    latest_evidence: dict[AgentFamilyId, StoredResearchAgentEvidence] = {}
    for stored in evidence:
        policy = policies[stored.evidence.agent_family_id]
        if not apply_debounce or stored.evidence.available_at + policy.debounce <= now:
            current = latest_evidence.get(stored.evidence.agent_family_id)
            if current is None or stored.sequence > current.sequence:
                latest_evidence[stored.evidence.agent_family_id] = stored
    due_work: dict[AgentFamilyId, ResearchAgentOpenWorkV1] = {}
    for item in open_work:
        if (
            item.state is ResearchAgentOpenWorkState.OPEN
            and item.next_wake_at is not None
            and item.next_wake_at <= now
        ):
            current = due_work.get(item.agent_family_id)
            if current is None or current.next_wake_at is None or item.next_wake_at < current.next_wake_at:
                due_work[item.agent_family_id] = item
    candidates: list[RunnableResearchActor] = []
    for policy in ACTOR_WAKE_POLICIES:
        state = state_by_family.get(policy.family_id)
        if state is not None and state.cooldown_until is not None and state.cooldown_until > now:
            continue
        item = due_work.get(policy.family_id)
        stored = latest_evidence.get(policy.family_id)
        candidate = _candidate_for_actor(ActorWakeEvaluation(policy, state, stored, item, now))
        if candidate is not None:
            candidates.append(candidate)
    policy_order = {policy.family_id: index for index, policy in enumerate(ACTOR_WAKE_POLICIES)}
    oldest = dt.datetime.min.replace(tzinfo=dt.UTC)
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.priority,
                (state_by_family.get(item.agent_family_id) or _empty_state(item.agent_family_id)).last_terminal_at
                or oldest,
                policy_order[item.agent_family_id],
            ),
        )
    )


def _candidate_for_actor(
    evaluation: ActorWakeEvaluation,
) -> RunnableResearchActor | None:
    policy = evaluation.policy
    state = evaluation.state
    evidence = evaluation.evidence
    open_work = evaluation.open_work
    now = evaluation.now
    if open_work is not None:
        return RunnableResearchActor(
            policy.family_id,
            ResearchActorWakeReason.OPEN_WORK,
            evidence,
            open_work,
            _OPEN_WORK_PRIORITY,
        )
    if evidence is not None:
        retry = _retry_reason(state, evidence, now)
        if retry is False:
            return None
        if retry is True:
            return RunnableResearchActor(
                policy.family_id,
                ResearchActorWakeReason.RETRY,
                evidence,
                None,
                _RETRY_PRIORITY,
            )
        reason, priority = _evidence_reason(policy, evidence)
        return RunnableResearchActor(policy.family_id, reason, evidence, None, priority)
    if _scheduled_due(policy, state, now):
        return RunnableResearchActor(
            policy.family_id,
            ResearchActorWakeReason.SCHEDULED,
            None,
            None,
            _SCHEDULED_PRIORITY,
        )
    return None


def _retry_reason(
    state: ActorWakeState | None,
    evidence: StoredResearchAgentEvidence,
    now: dt.datetime,
) -> bool | None:
    if (
        state is None
        or state.consecutive_failures == 0
        or state.last_failed_evidence_id != evidence.evidence.evidence_id
    ):
        return None
    delay = retry_delay(state.consecutive_failures)
    if delay is None or state.last_terminal_at is None:
        return False
    return state.last_terminal_at + delay <= now


def _evidence_reason(
    policy: ActorWakePolicy,
    evidence: StoredResearchAgentEvidence,
) -> tuple[ResearchActorWakeReason, int]:
    match evidence.evidence.trigger_kind:
        case ResearchAgentTriggerKind.REVIEWER_FEEDBACK:
            return ResearchActorWakeReason.REVIEWER_FEEDBACK, _REVIEWER_PRIORITY
        case ResearchAgentTriggerKind.OPEN_WORK:
            return ResearchActorWakeReason.CURRENT_SESSION, _OPEN_WORK_PRIORITY
        case (
            ResearchAgentTriggerKind.NEW_DATA
            | ResearchAgentTriggerKind.MARKET_EVENT
            | ResearchAgentTriggerKind.EXPERIMENT_RESULT
            | ResearchAgentTriggerKind.SCHEDULED_WAKE
        ):
            match policy.family_id:
                case "day_trading":
                    return ResearchActorWakeReason.CURRENT_SESSION, policy.priority
                case (
                    "opportunity_manager"
                    | "swing_trading"
                    | "systematic_quant"
                    | "derivatives_research"
                    | "market_context"
                ):
                    return ResearchActorWakeReason.SOURCE_EVENT, policy.priority
                case unreachable:
                    assert_never(unreachable)
        case unreachable:
            assert_never(unreachable)


def _scheduled_due(policy: ActorWakePolicy, state: ActorWakeState | None, now: dt.datetime) -> bool:
    return (
        policy.scheduled_interval is not None
        and state is not None
        and state.last_terminal_at is not None
        and state.consecutive_failures == 0
        and state.last_terminal_at + policy.scheduled_interval <= now
    )


def _empty_state(family: AgentFamilyId) -> ActorWakeState:
    return ActorWakeState(family, None, None, 0, None)


__all__ = (
    "ACTOR_WAKE_POLICIES",
    "ActorWakeEvaluation",
    "ActorWakePolicy",
    "ActorWakeState",
    "ResearchActorWakeReason",
    "RunnableResearchActor",
    "retry_delay",
    "runnable_actors",
)
