from __future__ import annotations

import multiprocessing
import threading
from multiprocessing.synchronize import Event
from pathlib import Path

from tests.autonomous_supervisor_fixtures import fixture_client_reasoner, now_clock, zero_clock
from tests.test_autonomous_supervisor_adapter import NOW, _evidence
from trading_agent._autonomous_supervisor_steps import InvalidAutonomousSupervisorError
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.autonomous_reasoning import AutonomousDefer
from trading_agent.autonomous_supervisor_adapter import AutonomousSupervisorAdapter
from trading_agent.autonomous_supervisor_runtime import AutonomousSupervisorRuntime
from trading_agent.autonomous_task_store import AutonomousTaskStore, AutonomousTaskStoreError
from trading_agent.autonomous_tool_runtime import AutonomousToolRuntime
from trading_agent.research_agent_cycle_models import ResearchAgentEvidenceV1


def _adapter(path: Path) -> AutonomousSupervisorAdapter:
    wait = AutonomousDefer(
        reason="Wait for the next deterministic evidence review boundary.",
        resume_condition="Resume when the next scheduled evidence review boundary opens.",
        next_wake_at=NOW,
    )
    runtime = AutonomousSupervisorRuntime(
        tasks=AutonomousTaskStore(path / "tasks.sqlite3"),
        memories=AutonomousMemoryStore(path / "memories.sqlite3"),
        reasoner=fixture_client_reasoner(wait),
        tools=AutonomousToolRuntime((), now_clock, worker_modules=frozenset()),
        wall_clock=now_clock,
        monotonic=zero_clock,
    )
    return AutonomousSupervisorAdapter(runtime)


def _process_admit(path: str, evidence: ResearchAgentEvidenceV1, start: Event) -> None:
    assert start.wait(10)
    _ = _adapter(Path(path)).admit_evidence(evidence, NOW)


def test_concurrent_exact_replay_is_idempotent_across_threads(tmp_path: Path) -> None:
    # Given: two threads are ready to admit the exact same evidence identity.
    adapter = _adapter(tmp_path)
    evidence = _evidence("day_trading", "a", market="us_equities", subjects=("AAPL",))
    barrier = threading.Barrier(2)
    task_ids: list[str] = []
    failures: list[InvalidAutonomousSupervisorError | AutonomousTaskStoreError] = []

    def admit() -> None:
        barrier.wait()
        try:
            task_ids.append(str(adapter.admit_evidence(evidence, NOW).task_id))
        except (InvalidAutonomousSupervisorError, AutonomousTaskStoreError) as error:
            failures.append(error)

    # When: both admissions run concurrently.
    workers = (threading.Thread(target=admit), threading.Thread(target=admit))
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)

    # Then: both calls resolve to one root without a raw store conflict.
    assert not any(worker.is_alive() for worker in workers)
    assert failures == []
    assert len(set(task_ids)) == 1
    assert len(adapter.runtime.tasks.reader().tasks()) == 1


def test_overlapping_evidence_has_one_root_across_processes(tmp_path: Path) -> None:
    # Given: independent processes carry nonidentical subject sets with one overlap.
    first = _evidence("day_trading", "a", market="us_equities", subjects=("AAPL", "MSFT"))
    second = _evidence("day_trading", "b", market="us_equities", subjects=("MSFT", "NVDA"))
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    workers = tuple(
        context.Process(target=_process_admit, args=(str(tmp_path), evidence, start))
        for evidence in (first, second)
    )

    # When: both processes admit their evidence from the same release boundary.
    for worker in workers:
        worker.start()
    start.set()
    for worker in workers:
        worker.join(15)

    # Then: one root retains both immutable evidence identities.
    tasks = AutonomousTaskStore(tmp_path / "tasks.sqlite3").reader().tasks()
    assert tuple(worker.exitcode for worker in workers) == (0, 0)
    assert len(tasks) == 1
    assert set(tasks[0].source_evidence_ids) == {first.evidence_id, second.evidence_id}


def test_concurrent_exact_content_conflict_fails_typed_without_duplicate_root(tmp_path: Path) -> None:
    # Given: two threads use one evidence identity with different canonical content.
    adapter = _adapter(tmp_path)
    canonical = _evidence("day_trading", "a", market="us_equities", subjects=("AAPL",))
    conflicting = _evidence(
        "day_trading",
        "a",
        market="us_equities",
        subjects=("AAPL",),
        payload='{"price":71000}',
    )
    barrier = threading.Barrier(2)
    accepted: list[str] = []
    rejected: list[InvalidAutonomousSupervisorError | AutonomousTaskStoreError] = []

    def admit(evidence: ResearchAgentEvidenceV1) -> None:
        barrier.wait()
        try:
            accepted.append(str(adapter.admit_evidence(evidence, NOW).task_id))
        except (InvalidAutonomousSupervisorError, AutonomousTaskStoreError) as error:
            rejected.append(error)

    # When: both identities cross the admission boundary concurrently.
    workers = tuple(threading.Thread(target=admit, args=(evidence,)) for evidence in (canonical, conflicting))
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(10)

    # Then: one canonical value wins and the other receives only the typed replay conflict.
    tasks = adapter.runtime.tasks.reader().tasks()
    assert not any(worker.is_alive() for worker in workers)
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert isinstance(rejected[0], InvalidAutonomousSupervisorError)
    assert str(rejected[0]) == "autonomous_evidence_replay_conflict"
    assert len(tasks) == 1
    assert tasks[0].source_evidence_ids == (canonical.evidence_id,)
