from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from tests.test_kr_autonomous_outcome_learning import _paths
from tests.test_kr_loop_engineer_cli import _bundle, _memory_record
from tests.test_kr_loop_engineer_controller import _shadow
from tests.test_kr_loop_engineer_mutation import _EditingWorker, _repository
from trading_agent.autonomous_memory_store import AutonomousMemoryStore
from trading_agent.kr_loop_engineer_controller import KrLoopEngineerController
from trading_agent.kr_loop_engineer_models import KrLoopCandidateState, KrLoopHealthReceipt
from trading_agent.kr_loop_engineer_mutation import KrLoopMutationExecutor
from trading_agent.kr_loop_engineer_policy import mutation_contract
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore
from trading_agent.kr_loop_engineer_sync import sync_kr_loop_bundles

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 27, 18, 0, tzinfo=KST)


def test_repeated_failure_reaches_paper_release_and_restart_safe_rollback(tmp_path: Path) -> None:
    # Given: a real isolated Git source, durable failure bundle, and bounded fixture coding worker.
    repository, base = _repository(tmp_path / "source")
    paths = _paths(tmp_path / "state")
    bundle = _bundle()
    with AutonomousMemoryStore(paths.memory_database).writer() as writer:
        assert writer.append(_memory_record(bundle))
    assert sync_kr_loop_bundles(paths, base_commit=base, now=NOW).inserted == 1
    contract = mutation_contract(bundle, base)
    executor = KrLoopMutationExecutor(
        repository=repository,
        task_root=paths.loop_task_root,
        artifact_root=paths.loop_artifact_root,
        worker=_EditingWorker(contract.allowed_paths[0]),
    )
    store = KrLoopEngineerStore(paths.loop_database)
    controller = KrLoopEngineerController(store, executor)

    # When: mutation, independent checks, two future shadows, promotion, and a health breach run.
    shadowing = controller.mutate(bundle, now=NOW + dt.timedelta(minutes=1))
    first = controller.record_shadow(shadowing.candidate_id, _shadow(1))
    promoted = controller.record_shadow(shadowing.candidate_id, _shadow(2))
    release = store.releases()[-1]
    health = KrLoopHealthReceipt(
        release_id=release.release_id,
        observed_at=NOW + dt.timedelta(days=3),
        error_rate=Decimal("0.06"),
        data_eligibility_failures=0,
        order_mismatches=0,
        research_task_losses=0,
        evidence_refs=("health:error-rate",),
    )
    rolled_back = controller.record_health(health)
    restarted = KrLoopEngineerController(KrLoopEngineerStore(paths.loop_database), executor)
    replay = restarted.record_health(health)

    # Then: all immutable states remain, promotion waited for session two, and rollback is exactly once.
    assert shadowing.state is first.state is KrLoopCandidateState.SHADOWING
    assert promoted.state is KrLoopCandidateState.PROMOTED
    assert replay == rolled_back
    assert tuple(item.state for item in store.snapshots()) == (
        KrLoopCandidateState.DETECTED,
        KrLoopCandidateState.CANDIDATE_READY,
        KrLoopCandidateState.SHADOWING,
        KrLoopCandidateState.SHADOWING,
        KrLoopCandidateState.SHADOWING,
        KrLoopCandidateState.PROMOTED,
        KrLoopCandidateState.ROLLED_BACK,
    )
    assert tuple(item.action.value for item in store.releases()) == ("promote", "rollback")
    assert store.releases()[-1].active_commit == base
    assert len(tuple(paths.loop_artifact_root.glob("*.patch"))) == 1
    assert not tuple(paths.loop_task_root.iterdir())
