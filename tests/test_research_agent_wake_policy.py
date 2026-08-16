from __future__ import annotations

import datetime as dt
import hashlib

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_cycle_models import (
    CycleId,
    EvidenceId,
    ResearchAgentEvidenceV1,
    ResearchAgentOpenWorkState,
    ResearchAgentOpenWorkV1,
    ResearchAgentTriggerKind,
)
from trading_agent.research_agent_cycle_store import StoredResearchAgentEvidence
from trading_agent.research_agent_wake_policy import (
    ActorWakeState,
    ResearchActorWakeReason,
    retry_delay,
    runnable_actors,
)

NOW = dt.datetime(2026, 8, 2, 12, 0, tzinfo=dt.UTC)


def _stored_evidence(
    family: AgentFamilyId,
    *,
    trigger: ResearchAgentTriggerKind = ResearchAgentTriggerKind.NEW_DATA,
    available_at: dt.datetime = NOW,
    sequence: int = 1,
) -> StoredResearchAgentEvidence:
    digest = hashlib.sha256(f"{family}:{trigger}:{sequence}".encode()).hexdigest()
    return StoredResearchAgentEvidence(
        sequence=sequence,
        evidence=ResearchAgentEvidenceV1(
            evidence_id=EvidenceId(digest),
            agent_family_id=family,
            trigger_kind=trigger,
            source_key=f"wake.{family}.{sequence}",
            evidence_refs=(digest,),
            observed_at=available_at,
            available_at=available_at,
            payload_sha256=digest,
            market_id="none",
        ),
    )


def test_no_new_evidence_means_no_runnable_actor() -> None:
    assert runnable_actors((), (), now=NOW) == ()


def test_opportunity_debounces_while_systematic_feedback_runs_immediately() -> None:
    opportunity = _stored_evidence("opportunity_manager")
    feedback = _stored_evidence("systematic_quant", trigger=ResearchAgentTriggerKind.REVIEWER_FEEDBACK)

    selected = runnable_actors((opportunity, feedback), (), now=NOW + dt.timedelta(seconds=30))
    later = runnable_actors((opportunity, feedback), (), now=NOW + dt.timedelta(minutes=2))

    assert tuple(item.agent_family_id for item in selected) == ("systematic_quant",)
    assert tuple(item.agent_family_id for item in later) == ("systematic_quant", "opportunity_manager")


def test_failure_backoff_is_15_minutes_then_1_hour_then_4_hours() -> None:
    assert tuple(retry_delay(count) for count in range(1, 5)) == (
        dt.timedelta(minutes=15),
        dt.timedelta(hours=1),
        dt.timedelta(hours=4),
        None,
    )


def test_due_open_work_preempts_reviewer_and_future_work_does_not_run() -> None:
    due = ResearchAgentOpenWorkV1(
        work_id="day-open-work-001",
        cycle_id=CycleId("a" * 64),
        agent_family_id="day_trading",
        state=ResearchAgentOpenWorkState.OPEN,
        evidence_refs=("a" * 64,),
        next_wake_at=NOW,
        updated_at=NOW - dt.timedelta(minutes=1),
    )
    future = due.model_copy(
        update={
            "work_id": "swing-open-work-001",
            "agent_family_id": "swing_trading",
            "next_wake_at": NOW + dt.timedelta(minutes=1),
        }
    )
    feedback = _stored_evidence("systematic_quant", trigger=ResearchAgentTriggerKind.REVIEWER_FEEDBACK)

    selected = runnable_actors((feedback,), (due, future), now=NOW)

    assert tuple(item.agent_family_id for item in selected) == ("day_trading", "systematic_quant")
    assert selected[0].reason is ResearchActorWakeReason.OPEN_WORK


def test_cooldown_and_terminal_failure_wait_for_new_evidence() -> None:
    original = _stored_evidence("systematic_quant")
    state = ActorWakeState(
        agent_family_id="systematic_quant",
        last_terminal_at=NOW - dt.timedelta(hours=5),
        cooldown_until=NOW + dt.timedelta(minutes=1),
        consecutive_failures=4,
        last_failed_evidence_id=original.evidence.evidence_id,
    )
    assert runnable_actors((original,), (), now=NOW, states=(state,)) == ()
    replacement = _stored_evidence("systematic_quant", sequence=2)

    assert runnable_actors(
        (replacement,),
        (),
        now=NOW + dt.timedelta(minutes=1),
        states=(state,),
    )[0].agent_family_id == "systematic_quant"


def test_distinct_new_evidence_bypasses_prior_failure_cooldown() -> None:
    original = _stored_evidence("systematic_quant")
    replacement = _stored_evidence("systematic_quant", sequence=2)
    state = ActorWakeState(
        agent_family_id="systematic_quant",
        last_terminal_at=NOW,
        cooldown_until=NOW + dt.timedelta(minutes=15),
        consecutive_failures=1,
        last_failed_evidence_id=original.evidence.evidence_id,
    )

    selected = runnable_actors((replacement,), (), now=NOW, states=(state,))

    assert tuple(item.agent_family_id for item in selected) == ("systematic_quant",)
    assert selected[0].reason is ResearchActorWakeReason.SOURCE_EVENT


def test_scheduled_actor_requires_prior_terminal_and_round_robins_oldest_first() -> None:
    states = (
        ActorWakeState(
            agent_family_id="market_context",
            last_terminal_at=NOW - dt.timedelta(hours=1),
            cooldown_until=None,
            consecutive_failures=0,
            last_failed_evidence_id=None,
        ),
        ActorWakeState(
            agent_family_id="derivatives_research",
            last_terminal_at=NOW - dt.timedelta(hours=2),
            cooldown_until=None,
            consecutive_failures=0,
            last_failed_evidence_id=None,
        ),
    )

    selected = runnable_actors((), (), now=NOW, states=states)

    assert tuple(item.agent_family_id for item in selected) == ("derivatives_research", "market_context")
    assert all(item.reason is ResearchActorWakeReason.SCHEDULED for item in selected)
