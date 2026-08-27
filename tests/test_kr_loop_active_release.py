from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.test_kr_loop_engineer_mutation import NOW, _bundle, _EditingWorker, _git, _repository
from trading_agent.kr_autonomous_outcome_models import KrLoopFailureCode
from trading_agent.kr_loop_active_release import (
    InvalidKrLoopActiveReleaseError,
    active_release_for_event,
    bootstrap_active_release,
    load_active_release,
    replace_active_release,
    resolve_active_source,
)
from trading_agent.kr_loop_engineer_models import (
    KrLoopCandidateState,
    KrLoopReleaseAction,
    build_candidate_snapshot,
    build_release_event,
)
from trading_agent.kr_loop_engineer_mutation import KrLoopMutationExecutor
from trading_agent.kr_loop_engineer_policy import mutation_contract
from trading_agent.kr_loop_release_artifacts import KrLoopReleaseArtifactStore


def test_active_release_selects_verified_candidate_and_baseline(tmp_path: Path) -> None:
    repository, artifacts, candidate = _release(tmp_path)
    promoted = build_candidate_snapshot(
        bundle_id=candidate.bundle_id,
        base_commit=candidate.base_commit,
        allowed_paths=candidate.allowed_paths,
        state=KrLoopCandidateState.PROMOTED,
        updated_at=NOW,
        previous=candidate,
        candidate_commit=candidate.candidate_commit,
        patch_sha256=candidate.patch_sha256,
        verification_sha256="7" * 64,
        shadow_receipts=(_shadow(1), _shadow(2)),
    )
    promotion = build_release_event(
        action=KrLoopReleaseAction.PROMOTE,
        candidate=promoted,
        previous=None,
        recorded_at=NOW,
    )
    active_path = tmp_path / "active.json"

    promoted_active = active_release_for_event(repository, artifacts, promotion, NOW)
    assert replace_active_release(active_path, promoted_active) is True
    assert replace_active_release(active_path, promoted_active) is False
    assert (
        resolve_active_source(active_path, repository, artifacts)
        == artifacts.verified(candidate.candidate_id).candidate_root
    )

    rollback = build_release_event(
        action=KrLoopReleaseAction.ROLLBACK,
        candidate=promoted.model_copy(update={"state": KrLoopCandidateState.ROLLED_BACK, "health_receipts": ()}),
        previous=promotion,
        recorded_at=NOW + dt.timedelta(minutes=1),
    )
    baseline_active = active_release_for_event(repository, artifacts, rollback, NOW + dt.timedelta(minutes=1))
    assert replace_active_release(active_path, baseline_active) is True
    assert load_active_release(active_path).action == "baseline"
    assert (
        resolve_active_source(active_path, repository, artifacts)
        == artifacts.verified(candidate.candidate_id).baseline_root
    )


def test_active_release_rejects_path_substitution(tmp_path: Path) -> None:
    repository, artifacts, candidate = _release(tmp_path)
    artifact = artifacts.verified(candidate.candidate_id)
    from trading_agent.kr_loop_active_release import KrLoopActiveRelease

    forged = KrLoopActiveRelease(
        generation=1,
        release_id="8" * 64,
        candidate_id=candidate.candidate_id,
        action="candidate",
        source_root=repository,
        active_commit=candidate.candidate_commit or "0" * 40,
        applied_at=NOW,
    )
    path = tmp_path / "active.json"
    assert replace_active_release(path, forged)
    assert artifact.candidate_root != repository

    with pytest.raises(InvalidKrLoopActiveReleaseError):
        _ = resolve_active_source(path, repository, artifacts)


def test_bootstrap_active_release_runs_current_verified_repository(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path / "source")
    active = bootstrap_active_release(repository, base, NOW)
    path = tmp_path / "active.json"

    assert replace_active_release(path, active)
    assert load_active_release(path).generation == 0
    assert resolve_active_source(path, repository, KrLoopReleaseArtifactStore(tmp_path / "artifacts")) == repository


def _release(tmp_path: Path):
    repository, base = _repository(tmp_path / "source")
    (repository / "run_research_agent_runtime.py").write_text("print('fixture runtime')\n", encoding="utf-8")
    _git(repository, "add", "run_research_agent_runtime.py")
    _git(repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "runtime")
    base = _git(repository, "rev-parse", "HEAD").strip()
    bundle = _bundle(KrLoopFailureCode.CRITIC_CLUSTER_COUNT)
    changed = mutation_contract(bundle, base).allowed_paths[0]
    artifact_root = tmp_path / "artifacts"
    result = KrLoopMutationExecutor(
        repository=repository,
        task_root=tmp_path / "tasks",
        artifact_root=artifact_root,
        worker=_EditingWorker(changed),
    ).execute(bundle, base_commit=base, now=NOW)
    assert result.snapshot is not None
    return repository, KrLoopReleaseArtifactStore(artifact_root), result.snapshot


def _shadow(day: int):
    from decimal import Decimal

    from trading_agent.kr_loop_engineer_models import KrLoopShadowReceipt

    return KrLoopShadowReceipt(
        session_date=NOW.date() + dt.timedelta(days=day),
        observed_at=NOW + dt.timedelta(days=day),
        champion_score=Decimal("0.5"),
        challenger_score=Decimal("0.6"),
        error_count=0,
        data_eligibility_failures=0,
        order_mismatches=0,
        research_task_losses=0,
        evidence_refs=(f"shadow:{day}",),
    )
