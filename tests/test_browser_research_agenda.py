from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.autonomous_supervisor_fixtures import fixture_reasoner, now_clock, zero_clock
from trading_agent._autonomous_supervisor_steps import CompletionPayload, payload_json, run_budget
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import AutonomousDefer
from trading_agent.autonomous_supervisor_adapter import AutonomousSupervisorAdapter
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_models import AutonomousAgentRole, AutonomousTaskState, AutonomousTaskStep
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.autonomous_tool_runtime import AutonomousToolRuntime
from trading_agent.browser_research_agenda import ContinuousBrowserResearchSupervisor, InvalidBrowserResearchAgendaError
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore

NOW = dt.datetime(2026, 8, 26, 12, 0, tzinfo=dt.UTC)


def agenda_services_fixture(tmp_path: Path) -> ContinuousBrowserResearchSupervisor:
    # Given: a real durable task/cycle-store pair and a reasoner that chooses a timed wait.
    runtime = AutonomousSupervisorRuntime(
        tasks=AutonomousTaskStore(tmp_path / "tasks.sqlite3"),
        memories=AutonomousMemoryStore(tmp_path / "memories.sqlite3"),
        reasoner=fixture_reasoner(
            tmp_path,
            (
                AutonomousDefer(
                    reason="No new evidence is actionable; retain the durable research task for a later review.",
                    resume_condition="Resume when the next bounded research review time is reached.",
                    next_wake_at=NOW + dt.timedelta(minutes=5),
                ),
            ),
        ),
        tools=AutonomousToolRuntime((), now_clock, worker_modules=frozenset()),
        wall_clock=now_clock,
        monotonic=zero_clock,
    )
    return ContinuousBrowserResearchSupervisor(
        AutonomousSupervisorAdapter(runtime), ResearchAgentCycleStore(tmp_path / "cycles.sqlite3")
    )


def complete_task(services: ContinuousBrowserResearchSupervisor, task_id: str, now: dt.datetime) -> None:
    # Given: the latest durable task projection is nonterminal.
    task = services.supervisor.runtime.tasks.reader().task(task_id)
    assert task is not None

    # When: its normal append-only task history receives an explicit terminal completion.
    step = AutonomousTaskStep(
        task_id=task.task_id,
        sequence=len(services.supervisor.runtime.tasks.reader().steps(task.task_id)) + 1,
        role=task.owner_role,
        agent_family_id=task.agent_family_id,
        market_scope=task.market_scope,
        root_source_evidence_id=task.root_source_evidence_id,
        agent_version=task.agent_version,
        state=AutonomousTaskState.COMPLETED,
        payload_json=payload_json(
            CompletionPayload(
                decision_hash="a" * 64,
                summary="The bounded agenda episode completed with its root evidence preserved.",
                completion_evidence_refs=task.evidence_refs,
            )
        ),
        source_evidence_ids=task.source_evidence_ids,
        evidence_refs=task.evidence_refs,
        working_memory_ids=task.working_memory_ids,
        budget=run_budget(0, 0, 0),
        occurred_at=now,
        terminal_reason="agenda_test_completed",
    )
    with services.supervisor.runtime.tasks.writer() as writer:
        assert writer.append_step(step)

    # Then: the task is durably terminal before the agenda checks for a successor.
    terminal = services.supervisor.runtime.tasks.reader().task(task_id)
    assert terminal is not None
    assert terminal.state is AutonomousTaskState.COMPLETED


def test_agenda_creates_one_market_context_kr_task_and_replays_idempotently(tmp_path: Path) -> None:
    # Given: an empty durable agenda.
    services = agenda_services_fixture(tmp_path)

    # When: startup recovery calls ensure_open twice.
    first = services.ensure_open(NOW)
    second = services.ensure_open(NOW)

    # Then: the same Market Observer task and root evidence are retained exactly once.
    assert second.task_id == first.task_id
    assert first.agent_family_id == "market_context"
    assert first.market_scope == "kr_equities"
    assert first.owner_role is AutonomousAgentRole.MARKET_OBSERVER
    assert services.cycles.evidence(first.root_source_evidence_id) is not None
    assert len(services.episodes.all()) == 1


def test_terminal_episode_creates_a_lineage_linked_successor(tmp_path: Path) -> None:
    # Given: the agenda's current durable episode.
    services = agenda_services_fixture(tmp_path)
    predecessor = services.ensure_open(NOW)
    complete_task(services, str(predecessor.task_id), NOW)

    # When: the terminal state is observed on a later recovery tick.
    successor = services.ensure_open(NOW + dt.timedelta(seconds=30))

    # Then: exactly one new episode retains explicit predecessor lineage.
    assert successor.task_id != predecessor.task_id
    episode = services.episodes.get_by_task(successor.task_id)
    assert episode is not None
    assert episode.predecessor_task_id == predecessor.task_id
    assert len(services.episodes.all()) == 2


def test_episodes_stamp_initial_and_successor_from_ensure_open_time(tmp_path: Path) -> None:
    # Given: distinct non-midnight creation and successor recovery instants.
    services = agenda_services_fixture(tmp_path)
    initial_now = NOW + dt.timedelta(minutes=13, seconds=17)
    successor_now = NOW + dt.timedelta(hours=2, minutes=7, seconds=41)

    # When: the initial task is opened and its terminal predecessor rolls into a successor.
    predecessor = services.ensure_open(initial_now)
    predecessor_episode = services.episodes.get_by_task(predecessor.task_id)
    predecessor_evidence = services.cycles.evidence(predecessor.root_source_evidence_id)
    complete_task(services, str(predecessor.task_id), initial_now + dt.timedelta(minutes=1))
    successor = services.ensure_open(successor_now)
    successor_episode = services.episodes.get_by_task(successor.task_id)
    successor_evidence = services.cycles.evidence(successor.root_source_evidence_id)

    # Then: each persisted episode and synthetic evidence use its own validated ensure time.
    assert predecessor_episode is not None
    assert predecessor_evidence is not None
    assert successor_episode is not None
    assert successor_evidence is not None
    assert predecessor_episode.opened_at == initial_now
    assert predecessor_evidence.evidence.observed_at == initial_now
    assert predecessor_evidence.evidence.available_at == initial_now
    assert successor_episode.opened_at == successor_now
    assert successor_evidence.evidence.observed_at == successor_now
    assert successor_evidence.evidence.available_at == successor_now


def test_concurrent_ensure_open_creates_one_durable_episode(tmp_path: Path) -> None:
    # Given: two simultaneous startup callers sharing one task and cycle store.
    services = agenda_services_fixture(tmp_path)

    # When: both callers ensure the agenda is open at the same durable instant.
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = tuple(executor.map(services.ensure_open, (NOW, NOW)))

    # Then: task-store admission resolves both calls to the one root task and episode.
    assert first.task_id == second.task_id
    assert len(services.episodes.all()) == 1


def test_restart_after_terminal_replays_one_successor(tmp_path: Path) -> None:
    # Given: the prior process terminalized the current agenda episode.
    services = agenda_services_fixture(tmp_path)
    predecessor = services.ensure_open(NOW)
    complete_task(services, str(predecessor.task_id), NOW)
    services.cycles.close()
    resumed = ContinuousBrowserResearchSupervisor(
        services.supervisor, ResearchAgentCycleStore(tmp_path / "cycles.sqlite3")
    )

    # When: restart recovery ensures open work more than once.
    successor = resumed.ensure_open(NOW + dt.timedelta(minutes=1))
    replay = resumed.ensure_open(NOW + dt.timedelta(minutes=2))

    # Then: one stable successor preserves the predecessor link across the process boundary.
    assert replay.task_id == successor.task_id
    episode = resumed.episodes.get_by_task(successor.task_id)
    assert episode is not None
    assert episode.predecessor_task_id == predecessor.task_id
    assert len(resumed.episodes.all()) == 2


def test_agenda_does_not_encode_a_required_browser_tool_order(tmp_path: Path) -> None:
    # Given: an agenda task created from its canonical goal.
    task = agenda_services_fixture(tmp_path).ensure_open(NOW)

    # When: its supervisor-visible plan is inspected.
    # Then: it leaves browser action choice and order to the agent.
    plan_content = "\n".join(task.current_plan)
    assert "browser.search" not in plan_content
    assert "browser.open" not in plan_content
    assert "browser.read" not in plan_content


def test_close_releases_cycle_store_lease_and_is_idempotent(tmp_path: Path) -> None:
    # Given: a wrapper that owns the active cycle-store writer lease.
    services = agenda_services_fixture(tmp_path)
    cycle_path = services.cycles.path

    # When: close is called twice during normal shutdown.
    services.close()
    services.close()

    # Then: a fresh cycle store can acquire the exact same writer lease.
    with ResearchAgentCycleStore(cycle_path):
        pass


def test_close_releases_cycle_store_when_supervisor_close_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a supervisor whose close boundary raises unexpectedly.
    services = agenda_services_fixture(tmp_path)
    cycle_path = services.cycles.path

    def failing_close(_self: AutonomousSupervisorAdapter) -> None:
        raise RuntimeError("supervisor_close_failed")

    monkeypatch.setattr(AutonomousSupervisorAdapter, "close", failing_close)

    # When: wrapper shutdown propagates that failure.
    with pytest.raises(RuntimeError, match="supervisor_close_failed"):
        services.close()

    # Then: its owned cycle-store lease is nevertheless released.
    with ResearchAgentCycleStore(cycle_path):
        pass


def test_run_due_keeps_the_agenda_task_durable_at_a_timed_wait(tmp_path: Path) -> None:
    # Given: an empty agenda whose first due task elects a timed wait.
    services = agenda_services_fixture(tmp_path)

    # When: the continuous wrapper opens work before delegating the due run.
    results = services.run_due(NOW)

    # Then: the task remains open and the next same-time due call is idle rather than a busy loop.
    assert len(results) == 1
    assert results[0].result.status == "waiting"
    assert services.run_due(NOW) == ()
    task = services.ensure_open(NOW)
    assert task.state is AutonomousTaskState.WAITING_TIME


def test_agenda_rejects_a_naive_clock_before_durable_admission(tmp_path: Path) -> None:
    # Given: an empty agenda and an invalid local timestamp.
    services = agenda_services_fixture(tmp_path)

    # When/Then: no task or root episode is persisted before the boundary rejects it.
    with pytest.raises(InvalidBrowserResearchAgendaError, match="agenda_time_invalid"):
        services.ensure_open(dt.datetime(2026, 8, 26, 12, 0))
    assert services.episodes.all() == ()
