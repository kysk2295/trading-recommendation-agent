from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pytest

from tests.autonomous_supervisor_fixtures import fixture_reasoner, now_clock, zero_clock
from trading_agent._autonomous_supervisor_steps import InvalidAutonomousSupervisorError
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import (
    AutonomousComplete,
    AutonomousDefer,
    AutonomousReasoningResponse,
    AutonomousSubmitArtifact,
)
from trading_agent.autonomous_supervisor_adapter import AutonomousSupervisorAdapter
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_models import AutonomousSupervisorTickResult
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolRuntime
from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.research_agent_cycle_models import (
    EvidenceId,
    MarketId,
    ResearchAgentCycleState,
    ResearchAgentCycleV1,
    ResearchAgentEvidenceV1,
    ResearchAgentResultStatus,
    ResearchAgentTriggerKind,
    ResearchAgentWakeKind,
    research_agent_action_id,
    research_agent_cycle_id,
)

NOW = dt.datetime(2026, 8, 26, 12, 0, tzinfo=dt.UTC)


def _evidence(
    family: AgentFamilyId,
    identity: str,
    *,
    market: MarketId = "kr_equities",
    subjects: tuple[str, ...] = ("005930",),
    payload: str = '{"price":70000}',
) -> ResearchAgentEvidenceV1:
    return ResearchAgentEvidenceV1(
        evidence_id=EvidenceId(identity * 64),
        agent_family_id=family,
        trigger_kind=ResearchAgentTriggerKind.NEW_DATA,
        source_key=f"adapter.{family}.{identity}",
        evidence_refs=(hashlib.sha256(payload.encode()).hexdigest(),),
        observed_at=NOW,
        available_at=NOW,
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        market_id=market,
        bounded_payload_json=payload if subjects else None,
        subject_refs=subjects,
    )


def _adapter(tmp_path: Path, wakes: tuple[dt.datetime, ...]) -> AutonomousSupervisorAdapter:
    responses = tuple(
        AutonomousDefer(
            reason="Wait for the next deterministic evidence review boundary.",
            resume_condition="Resume when the next scheduled evidence review boundary opens.",
            next_wake_at=wake,
        )
        for wake in wakes
    )
    return _adapter_with_responses(tmp_path, responses)


def _adapter_with_responses(
    tmp_path: Path,
    responses: tuple[AutonomousReasoningResponse, ...],
) -> AutonomousSupervisorAdapter:
    runtime = AutonomousSupervisorRuntime(
        tasks=AutonomousTaskStore(tmp_path / "tasks.sqlite3"),
        memories=AutonomousMemoryStore(tmp_path / "memories.sqlite3"),
        reasoner=fixture_reasoner(tmp_path, responses),
        tools=AutonomousToolRuntime((), now_clock, worker_modules=frozenset()),
        wall_clock=now_clock,
        monotonic=zero_clock,
    )
    return AutonomousSupervisorAdapter(runtime)


def _cycle(evidence: ResearchAgentEvidenceV1) -> ResearchAgentCycleV1:
    cycle_id = research_agent_cycle_id(evidence, cursor_before=0)
    return ResearchAgentCycleV1(
        cycle_id=cycle_id,
        evidence_id=evidence.evidence_id,
        action_request_id=research_agent_action_id(cycle_id),
        agent_family_id=evidence.agent_family_id,
        market_id=evidence.market_id,
        evidence_sequence=1,
        cursor_before=0,
        state=ResearchAgentCycleState.STARTED,
        started_at=NOW,
    )


def test_related_evidence_appends_to_matching_open_task_without_changing_root(tmp_path: Path) -> None:
    # Given: two same-family, same-market evidence records share a non-empty subject.
    adapter = _adapter(
        tmp_path,
        (NOW + dt.timedelta(minutes=5), NOW + dt.timedelta(minutes=10)),
    )
    first = _evidence("day_trading", "a")
    second = _evidence("day_trading", "b")

    # When: both records are admitted in order.
    first_result = adapter.tick(first, NOW)
    second_result = adapter.tick(second, NOW + dt.timedelta(minutes=5))

    # Then: the durable task retains the first record as root and appends the second.
    task = adapter.runtime.tasks.reader().task(first_result.task_id or "")
    assert second_result.task_id == first_result.task_id
    assert task is not None
    assert task.root_source_evidence_id == first.evidence_id
    assert task.source_evidence_ids == (first.evidence_id, second.evidence_id)


@pytest.mark.parametrize("market", ("kr_equities", "us_equities"))
def test_exact_evidence_replay_is_idempotent(tmp_path: Path, market: MarketId) -> None:
    # Given: one admitted evidence record for a market-local task.
    adapter = _adapter(tmp_path, (NOW + dt.timedelta(minutes=5),))
    evidence = _evidence("day_trading", "a", market=market)
    first = adapter.tick(evidence, NOW)
    step_count = len(adapter.runtime.tasks.reader().steps(first.task_id or ""))

    # When: the exact same evidence is replayed before its wake.
    replay = adapter.tick(evidence, NOW + dt.timedelta(minutes=1))

    # Then: no duplicate durable step is added.
    assert replay.task_id == first.task_id
    assert len(adapter.runtime.tasks.reader().steps(first.task_id or "")) == step_count


@pytest.mark.parametrize(
    ("second_family", "second_market", "subjects"),
    (
        ("day_trading", "kr_equities", ()),
        ("swing_trading", "kr_equities", ("005930",)),
        ("day_trading", "us_equities", ("005930",)),
    ),
)
def test_unrelated_evidence_starts_a_new_task(
    tmp_path: Path,
    second_family: AgentFamilyId,
    second_market: MarketId,
    subjects: tuple[str, ...],
) -> None:
    # Given: a first open KR Day task and evidence outside one admission dimension.
    adapter = _adapter(
        tmp_path,
        (NOW + dt.timedelta(minutes=5), NOW + dt.timedelta(minutes=6)),
    )
    first = adapter.tick(_evidence("day_trading", "a"), NOW)
    second_evidence = _evidence(
        second_family,
        "b",
        market=second_market,
        subjects=subjects,
    )

    # When: the unrelated evidence is admitted.
    second = adapter.tick(second_evidence, NOW + dt.timedelta(minutes=1))

    # Then: it cannot change the first task's root identity.
    assert second.task_id != first.task_id
    assert len(adapter.runtime.tasks.reader().tasks()) == 2


def test_exact_evidence_content_conflict_fails_closed(tmp_path: Path) -> None:
    # Given: an exact evidence identity already admitted with canonical content.
    adapter = _adapter(tmp_path, (NOW + dt.timedelta(minutes=5),))
    original = _evidence("day_trading", "a")
    _ = adapter.tick(original, NOW)
    conflicting = _evidence("day_trading", "a", payload='{"price":71000}')

    # When/Then: replaying different content under that identity is rejected.
    with pytest.raises(InvalidAutonomousSupervisorError, match="autonomous_evidence_replay_conflict"):
        adapter.tick(conflicting, NOW + dt.timedelta(minutes=1))


def test_waiting_projection_preserves_exact_task_and_wake(tmp_path: Path) -> None:
    # Given: an admitted task reaches a scheduled autonomous wait.
    wake = NOW + dt.timedelta(minutes=5)
    adapter = _adapter(tmp_path, (wake,))
    evidence = _evidence("market_context", "a", market="cross_market")
    tick = adapter.tick(evidence, NOW)

    # When: the autonomous result is projected into the cycle audit contract.
    projected = adapter.project_tick(_cycle(evidence), tick, NOW)

    # Then: the exact durable identity and wake cross the boundary without artifacts.
    assert projected.status is ResearchAgentResultStatus.NO_ACTION
    assert projected.reason == "autonomous_task_waiting"
    assert projected.open_work_ref == str(tick.task_id)
    assert projected.artifact_refs == ()
    assert projected.next_wake_kind is ResearchAgentWakeKind.SCHEDULED
    assert projected.next_wake_at == wake


def test_completed_projection_requires_durable_evidence_linked_artifact(tmp_path: Path) -> None:
    # Given: the supervisor submits an evidence-linked artifact before explicit completion.
    evidence = _evidence("swing_trading", "a")
    evidence_ref = evidence.evidence_refs[0]
    adapter = _adapter_with_responses(
        tmp_path,
        (
            AutonomousSubmitArtifact(
                artifact_kind="hypothesis",
                artifact_json='{"hypothesis":"bounded"}',
                evidence_refs=(evidence_ref,),
                reason="Submit the durable evidence-linked hypothesis before completion.",
            ),
            AutonomousComplete(
                summary="The bounded hypothesis review completed with durable source evidence.",
                completion_evidence_refs=(evidence_ref,),
                reason="The durable hypothesis artifact supports explicit task completion.",
            ),
        ),
    )

    # When: the completed supervisor tick is projected.
    projected = adapter.project_tick(_cycle(evidence), adapter.tick(evidence, NOW), NOW)

    # Then: completion exposes only the durable artifact step reference.
    assert projected.status is ResearchAgentResultStatus.COMPLETED
    assert len(projected.artifact_refs) == 1


def test_event_wait_projection_preserves_exact_event_selector(tmp_path: Path) -> None:
    # Given: the autonomous task waits for one named market event.
    evidence = _evidence("opportunity_manager", "a")
    adapter = _adapter_with_responses(
        tmp_path,
        (
            AutonomousDefer(
                reason="Wait for the named opportunity evidence event before continuing.",
                resume_condition="Resume when the named opportunity evidence event arrives.",
                next_wake_event="opportunity_evidence",
            ),
        ),
    )

    # When: the event wait crosses the existing audit boundary.
    projected = adapter.project_tick(_cycle(evidence), adapter.tick(evidence, NOW), NOW)

    # Then: the event selector remains an event wake with no synthetic time.
    assert projected.next_wake_kind is ResearchAgentWakeKind.NEW_EVIDENCE
    assert projected.next_wake_at is None


def test_completed_projection_blocks_when_artifact_is_missing(tmp_path: Path) -> None:
    # Given: a durable task has only a wait step and no submitted artifact.
    evidence = _evidence("derivatives_research", "a")
    adapter = _adapter(tmp_path, (NOW + dt.timedelta(minutes=5),))
    waiting = adapter.tick(evidence, NOW)
    malformed = AutonomousSupervisorTickResult(
        status="completed",
        task_id=waiting.task_id,
        agent_family_id=waiting.agent_family_id,
        market_scope=waiting.market_scope,
    )

    # When: the malformed completion is projected.
    projected = adapter.project_tick(_cycle(evidence), malformed, NOW)

    # Then: completion fails closed without publication artifacts.
    assert projected.status is ResearchAgentResultStatus.BLOCKED
    assert projected.reason == "autonomous_task_completed_shape_invalid"
    assert projected.artifact_refs == ()


def test_exact_completed_evidence_replay_is_idempotent(tmp_path: Path) -> None:
    # Given: exact evidence has already produced an artifact-backed terminal task.
    evidence = _evidence("systematic_quant", "a")
    evidence_ref = evidence.evidence_refs[0]
    adapter = _adapter_with_responses(
        tmp_path,
        (
            AutonomousSubmitArtifact(
                artifact_kind="hypothesis",
                artifact_json='{"hypothesis":"bounded"}',
                evidence_refs=(evidence_ref,),
                reason="Submit the evidence-linked hypothesis before terminal replay.",
            ),
            AutonomousComplete(
                summary="The bounded systematic hypothesis completed with durable evidence.",
                completion_evidence_refs=(evidence_ref,),
                reason="The artifact supports terminal systematic task completion.",
            ),
        ),
    )
    first = adapter.tick(evidence, NOW)
    step_count = len(adapter.runtime.tasks.reader().steps(first.task_id or ""))

    # When: the exact content is replayed after completion.
    replay = adapter.tick(evidence, NOW + dt.timedelta(minutes=1))

    # Then: the terminal result is stable and no durable record is duplicated.
    assert replay.status == "completed"
    assert len(adapter.runtime.tasks.reader().steps(first.task_id or "")) == step_count
