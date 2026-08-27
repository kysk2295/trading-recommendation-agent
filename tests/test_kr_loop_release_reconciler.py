from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_kr_loop_active_release import NOW, _release, _shadow
from trading_agent.kr_loop_active_release import load_active_release
from trading_agent.kr_loop_engineer_controller import KrLoopEngineerController
from trading_agent.kr_loop_engineer_models import KrLoopCandidateState, build_candidate_snapshot
from trading_agent.kr_loop_engineer_store import KrLoopEngineerStore
from trading_agent.kr_loop_release_reconciler import (
    InvalidKrLoopReleaseReconciliationError,
    reconcile_active_release,
)


class _UnusedMutation:
    def execute(self, *args, **kwargs):
        raise AssertionError("not used")


def test_reconciler_cuts_over_once_and_restores_manifest_on_restart_failure(tmp_path: Path) -> None:
    repository, artifacts, ready = _release(tmp_path)
    store = KrLoopEngineerStore(tmp_path / "loop.sqlite3")
    detected = build_candidate_snapshot(
        bundle_id=ready.bundle_id,
        base_commit=ready.base_commit,
        allowed_paths=ready.allowed_paths,
        state=KrLoopCandidateState.DETECTED,
        updated_at=ready.created_at,
    )
    assert store.append(detected)
    assert store.append(ready.model_copy(update={"previous_snapshot_id": detected.snapshot_id}))
    shadowing = build_candidate_snapshot(
        bundle_id=ready.bundle_id,
        base_commit=ready.base_commit,
        allowed_paths=ready.allowed_paths,
        state=KrLoopCandidateState.SHADOWING,
        updated_at=NOW,
        previous=store.latest(ready.candidate_id),
        candidate_commit=ready.candidate_commit,
        patch_sha256=ready.patch_sha256,
        verification_sha256="7" * 64,
    )
    assert store.append(shadowing)
    controller = KrLoopEngineerController(store, _UnusedMutation())
    _ = controller.record_shadow(ready.candidate_id, _shadow(1))
    _ = controller.record_shadow(ready.candidate_id, _shadow(2))
    calls: list[tuple[str, ...]] = []

    first = reconcile_active_release(
        store=store,
        artifacts=artifacts,
        repository=repository,
        active_path=tmp_path / "active.json",
        runner=lambda command: calls.append(command) or 0,
        now=NOW,
    )
    second = reconcile_active_release(
        store=store,
        artifacts=artifacts,
        repository=repository,
        active_path=tmp_path / "active.json",
        runner=lambda command: calls.append(command) or 0,
        now=NOW,
    )

    assert first.changed is True and first.restarted is True
    assert second.changed is False and second.restarted is False
    assert calls == [
        (
            "/bin/launchctl",
            "kickstart",
            "-k",
            "gui/" + str(__import__("os").getuid()) + "/ai.trading-agent.research-agent-runtime",
        )
    ]
    assert load_active_release(tmp_path / "active.json").action == "candidate"

    failed_path = tmp_path / "failed-active.json"
    with pytest.raises(InvalidKrLoopReleaseReconciliationError):
        _ = reconcile_active_release(
            store=store,
            artifacts=artifacts,
            repository=repository,
            active_path=failed_path,
            runner=lambda _command: 1,
            now=NOW,
        )
    assert load_active_release(failed_path).action == "baseline"
