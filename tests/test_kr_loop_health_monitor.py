from __future__ import annotations

import datetime as dt
from pathlib import Path

from tests.test_kr_loop_active_release import NOW, _release, _shadow
from tests.test_research_agent_service_cli import _config
from trading_agent.kr_loop_active_release import load_active_release
from trading_agent.kr_loop_engineer_controller import KrLoopEngineerController
from trading_agent.kr_loop_engineer_models import KrLoopCandidateState, build_candidate_snapshot
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore
from trading_agent.kr_loop_health_monitor import build_active_health_receipt, monitor_active_release
from trading_agent.kr_loop_release_reconciler import reconcile_active_release
from trading_agent.research_agent_service_config import canonical_research_agent_service_config_sha256
from trading_agent.research_agent_service_health import (
    health_for_service_report,
    write_persisted_research_agent_service_health,
)


class _UnusedMutation:
    def execute(self, *args, **kwargs):
        raise AssertionError("not used")


def test_fresh_matching_service_health_is_accepted_without_fabricated_failures(tmp_path: Path) -> None:
    config = _config(tmp_path / "service")
    release_id = "9" * 64
    observed_at = NOW + dt.timedelta(minutes=1)
    write_persisted_research_agent_service_health(
        config.output_root,
        health_for_service_report(canonical_research_agent_service_config_sha256(config), observed_at, False),
    )

    receipt = build_active_health_receipt(
        release_id=release_id,
        promoted_at=NOW,
        config=config,
        observed_at=observed_at + dt.timedelta(seconds=30),
    )

    assert receipt.release_id == release_id
    assert receipt.error_rate == 0
    assert receipt.data_eligibility_failures == 0
    assert receipt.order_mismatches == 0
    assert receipt.research_task_losses == 0
    assert receipt.evidence_refs[0].startswith("service-health:")


def test_stale_runtime_health_automatically_rolls_back_active_release(tmp_path: Path) -> None:
    repository, artifacts, ready = _release(tmp_path)
    store, controller = _promoted_store(tmp_path, ready)
    config = _config(tmp_path / "service")
    write_persisted_research_agent_service_health(
        config.output_root,
        health_for_service_report(
            canonical_research_agent_service_config_sha256(config),
            NOW,
            False,
        ),
    )
    active_path = tmp_path / "active.json"
    calls: list[tuple[str, ...]] = []
    _ = reconcile_active_release(
        store=store,
        artifacts=artifacts,
        repository=repository,
        active_path=active_path,
        runner=lambda command: calls.append(command) or 0,
        now=NOW,
    )

    result = monitor_active_release(
        controller=controller,
        config=config,
        artifacts=artifacts,
        repository=repository,
        active_path=active_path,
        observed_at=NOW + dt.timedelta(days=3),
        runner=lambda command: calls.append(command) or 0,
    )

    assert result.candidate.state is KrLoopCandidateState.ROLLED_BACK
    assert result.receipt.error_rate == 1
    assert result.reconciled is True
    assert load_active_release(active_path).action == "baseline"
    assert len(calls) == 2


def _promoted_store(tmp_path: Path, ready):
    store = KrLoopEngineerStore(tmp_path / "loop.sqlite3")
    detected = build_candidate_snapshot(
        bundle_id=ready.bundle_id,
        base_commit=ready.base_commit,
        allowed_paths=ready.allowed_paths,
        state=KrLoopCandidateState.DETECTED,
        updated_at=ready.created_at,
    )
    assert store.append(detected)
    candidate_ready = build_candidate_snapshot(
        bundle_id=ready.bundle_id,
        base_commit=ready.base_commit,
        allowed_paths=ready.allowed_paths,
        state=KrLoopCandidateState.CANDIDATE_READY,
        updated_at=ready.updated_at,
        previous=detected,
        candidate_commit=ready.candidate_commit,
        patch_sha256=ready.patch_sha256,
    )
    assert store.append(candidate_ready)
    shadowing = build_candidate_snapshot(
        bundle_id=ready.bundle_id,
        base_commit=ready.base_commit,
        allowed_paths=ready.allowed_paths,
        state=KrLoopCandidateState.SHADOWING,
        updated_at=NOW,
        previous=candidate_ready,
        candidate_commit=ready.candidate_commit,
        patch_sha256=ready.patch_sha256,
        verification_sha256="7" * 64,
    )
    assert store.append(shadowing)
    controller = KrLoopEngineerController(store, _UnusedMutation())
    _ = controller.record_shadow(ready.candidate_id, _shadow(1))
    promoted = controller.record_shadow(ready.candidate_id, _shadow(2))
    assert promoted.state is KrLoopCandidateState.PROMOTED
    return store, controller
