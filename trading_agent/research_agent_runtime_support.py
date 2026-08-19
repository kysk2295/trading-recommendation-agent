from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import assert_never

from trading_agent import us_equity_calendar as us_calendar
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES, AgentFamilyId
from trading_agent.research_agent_cycle_models import (
    MarketId,
    ResearchAgentCycleV1,
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkState,
    ResearchAgentOpenWorkV1,
    ResearchAgentResultStatus,
    ResearchAgentResultV1,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
    research_agent_result_id,
)
from trading_agent.research_agent_source_common import (
    ResearchAgentEvidenceMaterial,
    canonical_payload_json,
    interval_bucket,
)
from trading_agent.research_agent_sources import ResearchAgentSourceFailure
from trading_agent.research_agent_wake_policy import ActorWakeState, retry_delay


@dataclass(frozen=True, slots=True)
class RuntimeFailureContext:
    cycle: ResearchAgentCycleV1
    evidence: ResearchAgentEvidenceV1
    reason: str
    occurred_at: dt.datetime
    prior_failures: int


@dataclass(frozen=True, slots=True)
class ActorStateContext:
    cycle: ResearchAgentCycleV1
    evidence: ResearchAgentEvidenceV1
    result: ResearchAgentResultV1
    prior_failures: int


def source_failure_evidence(failure: ResearchAgentSourceFailure) -> ResearchAgentEvidenceV1:
    observed_at = interval_bucket(failure.observed_at, 15)
    payload = canonical_payload_json(
        {
            "family": failure.agent_family_id,
            "observed_at": observed_at.isoformat(),
            "reason": failure.reason,
        }
    )
    return ResearchAgentEvidenceMaterial(
        family=failure.agent_family_id,
        trigger=ResearchAgentTriggerKind.NEW_DATA,
        source_key=f"source_failure.{failure.reason}",
        observed_at=observed_at,
        available_at=observed_at,
        market_id="none",
        canonical_payload=payload,
    ).evidence()


def scheduled_evidence(family: AgentFamilyId, now: dt.datetime, minutes: int) -> ResearchAgentEvidenceV1:
    observed_at = interval_bucket(now, minutes)
    payload = canonical_payload_json(
        {"family": family, "observed_at": observed_at.isoformat(), "reason": "scheduled_wake"}
    )
    return ResearchAgentEvidenceMaterial(
        family=family,
        trigger=ResearchAgentTriggerKind.SCHEDULED_WAKE,
        source_key=f"scheduled.{family}.{observed_at.strftime('%Y%m%dT%H%M')}",
        observed_at=observed_at,
        available_at=observed_at,
        market_id=_scheduled_market(family),
        canonical_payload=payload,
    ).evidence()


def retry_evidence(item: ResearchAgentOpenWorkV1, now: dt.datetime) -> ResearchAgentEvidenceV1:
    market_id = _work_market(item)
    payload = canonical_payload_json(
        {
            "failure_count": item.failure_count,
            "family": item.agent_family_id,
            "observed_at": now.isoformat(),
            "work_id": item.work_id,
        }
    )
    return ResearchAgentEvidenceMaterial(
        family=item.agent_family_id,
        trigger=ResearchAgentTriggerKind.OPEN_WORK,
        source_key=f"retry.{item.agent_family_id}.{item.failure_count}",
        observed_at=now,
        available_at=now,
        market_id=market_id,
        canonical_payload=payload,
        subject_refs=(item.work_id,),
    ).evidence()


def runtime_failure_result(context: RuntimeFailureContext) -> ResearchAgentResultV1:
    failure_count = min(4, context.prior_failures + 1)
    delay = retry_delay(failure_count)
    wake_kind = ResearchAgentWakeKind.TERMINAL if delay is None else ResearchAgentWakeKind.SCHEDULED
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(context.cycle.cycle_id),
        cycle_id=context.cycle.cycle_id,
        agent_family_id=context.cycle.agent_family_id,
        market_id=context.cycle.market_id,
        status=ResearchAgentResultStatus.FAILED,
        question="Can this bounded research cycle continue safely?",
        summary="The cycle failed closed before any broker mutation.",
        reason=context.reason,
        continuation="Wait for the fixed retry deadline or new evidence.",
        open_work_ref=None,
        evidence_refs=context.evidence.evidence_refs,
        artifact_refs=(),
        occurred_at=context.occurred_at,
        next_wake_kind=wake_kind,
        next_wake_at=None if delay is None else context.occurred_at + delay,
    )


def primary_admission_no_action(
    cycle: ResearchAgentCycleV1,
    evidence: ResearchAgentEvidenceV1,
    now: dt.datetime,
) -> ResearchAgentResultV1 | None:
    match evidence.agent_family_id:
        case "opportunity_manager":
            blocked_prefix = "opportunity.blocked."
        case "market_context":
            blocked_prefix = "market_context.blocked."
        case "day_trading":
            blocked_prefix = "day.blocked."
        case "swing_trading" | "systematic_quant" | "derivatives_research":
            return None
        case unreachable:
            assert_never(unreachable)
    if evidence.source_key.startswith(blocked_prefix):
        reason = evidence.source_key
        continuation = "Wait for current-session Primary evidence that passes source admission."
        next_wake = None
    elif evidence.source_key.startswith(f"scheduled.{evidence.agent_family_id}."):
        current = now.astimezone(us_calendar.NEW_YORK)
        bounds = us_calendar.regular_session_bounds(current.date())
        if bounds is not None and bounds[0] <= current < bounds[1]:
            return None
        if bounds is not None and current < bounds[0]:
            next_wake = bounds[0]
        elif current.date() >= dt.date(max(us_calendar.PUBLISHED_CALENDAR_YEARS), 12, 31):
            next_wake = None
        else:
            next_wake = us_calendar.regular_session_bounds(us_calendar.next_regular_session(current.date()))
            next_wake = None if next_wake is None else next_wake[0]
        reason = f"{cycle.agent_family_id}.regular_session_closed"
        continuation = "Wait until the next New York regular session."
    else:
        return None
    return ResearchAgentResultV1(
        result_id=research_agent_result_id(cycle.cycle_id),
        cycle_id=cycle.cycle_id,
        agent_family_id=cycle.agent_family_id,
        market_id=cycle.market_id,
        status=ResearchAgentResultStatus.NO_ACTION,
        question="Can this Primary research cycle continue safely?",
        summary="Primary source admission produced a deterministic no-action result.",
        reason=reason,
        continuation=continuation,
        open_work_ref=None,
        evidence_refs=evidence.evidence_refs,
        artifact_refs=(),
        occurred_at=now,
        next_wake_kind=ResearchAgentWakeKind.NEW_EVIDENCE if next_wake is None else ResearchAgentWakeKind.SCHEDULED,
        next_wake_at=next_wake,
    )


def normalize_failure_backoff(
    result: ResearchAgentResultV1,
    prior_failures: int,
) -> ResearchAgentResultV1:
    match result.status:
        case ResearchAgentResultStatus.FAILED | ResearchAgentResultStatus.BLOCKED:
            failure_count = min(4, prior_failures + 1)
            delay = retry_delay(failure_count)
            return ResearchAgentResultV1.model_validate(
                result.model_dump(mode="python")
                | {
                    "next_wake_kind": (
                        ResearchAgentWakeKind.TERMINAL if delay is None else ResearchAgentWakeKind.SCHEDULED
                    ),
                    "next_wake_at": None if delay is None else result.occurred_at + delay,
                }
            )
        case ResearchAgentResultStatus.COMPLETED | ResearchAgentResultStatus.NO_ACTION:
            return result
        case unreachable:
            assert_never(unreachable)


def actor_wake_states(
    cycles: tuple[ResearchAgentCycleV1, ...],
    open_work: tuple[ResearchAgentOpenWorkV1, ...],
) -> tuple[ActorWakeState, ...]:
    cycle_by_actor = {_actor_key(cycle.agent_family_id, cycle.market_id): cycle for cycle in cycles}
    state_work = {
        _actor_key(item.agent_family_id, _work_market(item)): item
        for item in open_work
        if item.work_id.startswith("actor-state.")
    }
    actors = tuple(
        sorted(
            {*cycle_by_actor, *state_work},
            key=lambda item: (PRIMARY_AGENT_FAMILIES.index(item[0]), item[1]),
        )
    )
    return tuple(
        _actor_state(family, market, cycle_by_actor.get((family, market)), state_work.get((family, market)))
        for family, market in actors
    )


def actor_state_work(context: ActorStateContext) -> ResearchAgentOpenWorkV1:
    cycle = context.cycle
    evidence = context.evidence
    result = context.result
    prior_failures = context.prior_failures
    failed = result.status in {ResearchAgentResultStatus.FAILED, ResearchAgentResultStatus.BLOCKED}
    failure_count = min(4, prior_failures + 1) if failed else 0
    pending = result.open_work_ref is not None and result.reason in {
        "review_pending",
        "systematic_run_pending",
    }
    state = (
        ResearchAgentOpenWorkState.OPEN
        if (failed and failure_count < 4) or pending
        else ResearchAgentOpenWorkState.TERMINAL
    )
    return ResearchAgentOpenWorkV1(
        work_id=result.open_work_ref or _actor_state_work_id(cycle.agent_family_id, cycle.market_id),
        cycle_id=cycle.cycle_id,
        agent_family_id=cycle.agent_family_id,
        state=state,
        evidence_refs=result.evidence_refs,
        next_wake_at=result.next_wake_at if state is ResearchAgentOpenWorkState.OPEN else None,
        updated_at=result.occurred_at,
        source_evidence_id=evidence.evidence_id if failed else None,
        failure_count=failure_count,
    )


def _scheduled_market(family: AgentFamilyId) -> MarketId:
    match family:
        case "market_context":
            return "cross_market"
        case "opportunity_manager" | "day_trading" | "swing_trading" | "systematic_quant" | "derivatives_research":
            return "us_equities"
        case unreachable:
            assert_never(unreachable)


def _actor_state(
    family: AgentFamilyId,
    market_id: str,
    cycle: ResearchAgentCycleV1 | None,
    work: ResearchAgentOpenWorkV1 | None,
) -> ActorWakeState:
    failures = 0 if work is None else work.failure_count
    failed_evidence = None if work is None else work.source_evidence_id
    cooldown = (
        work.next_wake_at
        if work is not None and work.state is ResearchAgentOpenWorkState.OPEN and failures > 0
        else None
    )
    return ActorWakeState(
        family,
        None if cycle is None else cycle.terminal_at,
        cooldown,
        failures,
        failed_evidence,
        market_id,
    )


def _actor_key(family: AgentFamilyId, market_id: str) -> tuple[AgentFamilyId, str]:
    return family, market_id if family == "day_trading" else "none"


def _actor_state_work_id(family: AgentFamilyId, market_id: str) -> str:
    return f"actor-state.{family}.{market_id}" if family == "day_trading" else f"actor-state.{family}"


def _work_market(item: ResearchAgentOpenWorkV1) -> MarketId:
    prefix = "actor-state.day_trading."
    if item.work_id == "actor-state.day_trading":
        return "us_equities"
    for market_id in ("us_equities", "kr_equities", "cross_market", "none"):
        if item.work_id == f"{prefix}{market_id}":
            return market_id
    return "none"


__all__ = (
    "ActorStateContext",
    "RuntimeFailureContext",
    "actor_state_work",
    "actor_wake_states",
    "normalize_failure_backoff",
    "primary_admission_no_action",
    "retry_evidence",
    "runtime_failure_result",
    "scheduled_evidence",
    "source_failure_evidence",
)
