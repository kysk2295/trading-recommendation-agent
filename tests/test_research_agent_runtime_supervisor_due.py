from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.autonomous_supervisor_fixtures import fixture_reasoner, now_clock, zero_clock
from tests.test_research_agent_runtime import (
    EMPTY_COLLECTOR,
    NOW,
    RecordingArtifactActionClient,
    RecordingDecisionClient,
    StaticCollector,
    _evidence,
)
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import (
    AutonomousComplete,
    AutonomousDefer,
    AutonomousReasoningResponse,
    AutonomousSubmitArtifact,
)
from trading_agent.autonomous_supervisor_adapter import AutonomousSupervisorAdapter
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolRuntime
from trading_agent.hermes_delivery_reader import HermesDeliveryReader
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.research_agent_cycle_models import ResearchAgentOpenWorkState
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_hermes import project_research_agent_results
from trading_agent.research_agent_runtime import ResearchAgentRuntime, ResearchAgentRuntimeServices
from trading_agent.research_agent_sources import ResearchAgentSourceCollectionBatch


def _adapter(
    root: Path,
    marker: Path,
    responses: tuple[AutonomousReasoningResponse, ...] | None = None,
) -> AutonomousSupervisorAdapter:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    wakes = (NOW + dt.timedelta(minutes=5), NOW + dt.timedelta(minutes=10))
    configured = (
        tuple(
            AutonomousDefer(
                reason="Wait for the next deterministic autonomous review boundary.",
                resume_condition="Resume only when the exact scheduled boundary opens.",
                next_wake_at=wake,
            )
            for wake in wakes
        )
        if responses is None
        else responses
    )
    return AutonomousSupervisorAdapter(
        AutonomousSupervisorRuntime(
            tasks=AutonomousTaskStore(root / "tasks.sqlite3"),
            memories=AutonomousMemoryStore(root / "memories.sqlite3"),
            reasoner=fixture_reasoner(root, configured, marker=marker),
            tools=AutonomousToolRuntime((), now_clock, worker_modules=frozenset()),
            wall_clock=now_clock,
            monotonic=zero_clock,
        )
    )


def _runtime(
    cycle_path: Path,
    adapter: AutonomousSupervisorAdapter,
    collector: StaticCollector = EMPTY_COLLECTOR,
) -> ResearchAgentRuntime:
    return ResearchAgentRuntime(
        ResearchAgentRuntimeServices(
            ResearchAgentCycleStore(cycle_path),
            collector,
            RecordingDecisionClient([]),
            RecordingArtifactActionClient([]),
            supervisor_runtime=adapter,
        )
    )


def test_empty_collector_resumes_due_task_once_with_original_evidence(tmp_path: Path) -> None:
    cycle_path = tmp_path / "cycles.sqlite3"
    supervisor_root = tmp_path / "supervisor"
    marker = tmp_path / "reasoner-calls"
    original = _evidence("swing_trading", 1, "us_equities")
    responses: tuple[AutonomousReasoningResponse, ...] = (
        AutonomousDefer(
            reason="Wait for the next deterministic autonomous review boundary.",
            resume_condition="Resume only when the exact scheduled boundary opens.",
            next_wake_at=NOW + dt.timedelta(minutes=5),
        ),
        AutonomousSubmitArtifact(
            artifact_kind="hypothesis",
            artifact_json='{"hypothesis":"bounded due review"}',
            evidence_refs=original.evidence_refs,
            reason="Persist the evidence-linked hypothesis after the exact due wake.",
        ),
        AutonomousComplete(
            summary="The exact due wake completed with a durable evidence-linked hypothesis.",
            completion_evidence_refs=original.evidence_refs,
            reason="The durable artifact completes this bounded autonomous review.",
        ),
    )
    collector = StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(original,), failures=()))
    first_runtime = _runtime(cycle_path, _adapter(supervisor_root, marker, responses), collector)
    first = first_runtime.tick(NOW)
    first_runtime.close()

    due_at = NOW + dt.timedelta(minutes=5)
    due_runtime = _runtime(cycle_path, _adapter(supervisor_root, marker, responses))
    resumed = due_runtime.tick(due_at)
    results = due_runtime.store.results()
    cycles = due_runtime.store.latest_cycles()
    work = due_runtime.store.open_work("swing_trading")
    hermes_path = tmp_path / "hermes.sqlite3"
    with HermesDeliveryStore(hermes_path).writer() as writer:
        projected = project_research_agent_results(
            results,
            writer,
            evidence=due_runtime.store.all_evidence(),
            projected_result_ids=frozenset(),
        )
    due_runtime.close()

    replay_runtime = _runtime(cycle_path, _adapter(supervisor_root, marker, responses))
    replay = replay_runtime.tick(due_at)
    replay_results = replay_runtime.store.results()
    replay_runtime.close()

    assert first.status == "no_action"
    assert resumed.status == "completed"
    assert replay.status == "idle"
    assert len(results) == len(replay_results) == 2
    assert {cycle.evidence_id for cycle in cycles} == {original.evidence_id}
    assert len(work) == 1
    assert work[0].state is ResearchAgentOpenWorkState.TERMINAL
    assert work[0].updated_at == due_at
    assert projected.inserted == 1
    assert {event.source_event_id for event in HermesDeliveryReader(hermes_path).events()} == {
        result.result_id for result in results if result.status.value == "completed"
    }
    assert marker.read_text(encoding="utf-8").splitlines() == ["called", "called", "called"]


def test_waiting_event_requires_matching_admitted_evidence(tmp_path: Path) -> None:
    cycle_path = tmp_path / "cycles.sqlite3"
    supervisor_root = tmp_path / "supervisor"
    marker = tmp_path / "reasoner-calls"
    original = _evidence("swing_trading", 1, "us_equities")
    event_response = AutonomousDefer(
        reason="Wait for matching evidence admission before another bounded review.",
        resume_condition="Resume only after evidence for the same research subject is admitted.",
        next_wake_event="matching_evidence_admitted",
    )
    later_response = AutonomousDefer(
        reason="Wait after the matching evidence was reviewed at the durable boundary.",
        resume_condition="Resume at the next explicit scheduled review boundary.",
        next_wake_at=NOW + dt.timedelta(days=2),
    )
    responses = (event_response, later_response)
    initial = StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(original,), failures=()))
    seeded = _runtime(cycle_path, _adapter(supervisor_root, marker, responses), initial)
    assert seeded.tick(NOW).status == "no_action"
    seeded.close()

    tomorrow = NOW + dt.timedelta(days=1)
    empty = _runtime(cycle_path, _adapter(supervisor_root, marker, responses))
    assert empty.tick(tomorrow).status == "idle"
    empty.close()

    matching = _evidence("swing_trading", 2, "us_equities")
    admitted = StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(matching,), failures=()))
    resumed = _runtime(cycle_path, _adapter(supervisor_root, marker, responses), admitted)
    assert resumed.tick(tomorrow).status == "no_action"
    assert len(resumed.store.results()) == 2
    resumed.close()

    replay = _runtime(cycle_path, _adapter(supervisor_root, marker, responses))
    assert replay.tick(tomorrow).status == "idle"
    replay.close()
    assert marker.read_text(encoding="utf-8").splitlines() == ["called", "called"]


def test_restart_projects_advanced_task_without_second_reasoner_call(tmp_path: Path) -> None:
    cycle_path = tmp_path / "cycles.sqlite3"
    supervisor_root = tmp_path / "supervisor"
    marker = tmp_path / "reasoner-calls"
    original = _evidence("swing_trading", 1, "us_equities")
    collector = StaticCollector(ResearchAgentSourceCollectionBatch(evidence=(original,), failures=()))
    seeded = _runtime(cycle_path, _adapter(supervisor_root, marker), collector)
    assert seeded.tick(NOW).status == "no_action"
    seeded.close()

    due_at = NOW + dt.timedelta(minutes=5)
    crashed_adapter = _adapter(supervisor_root, marker)
    assert crashed_adapter.runtime.run_due(due_at)[0].status == "waiting"
    crashed_adapter.close()

    recovered = _runtime(cycle_path, _adapter(supervisor_root, marker))
    tick = recovered.tick(due_at + dt.timedelta(seconds=1))
    results = recovered.store.results()
    cycles = recovered.store.latest_cycles()
    recovered.close()

    replayed = _runtime(cycle_path, _adapter(supervisor_root, marker))
    assert replayed.tick(due_at + dt.timedelta(seconds=1)).status == "idle"
    assert len(replayed.store.results()) == 2
    replayed.close()

    assert tick.status == "no_action"
    assert len(results) == 2
    assert {cycle.evidence_id for cycle in cycles} == {original.evidence_id}
    assert marker.read_text(encoding="utf-8").splitlines() == ["called", "called"]
