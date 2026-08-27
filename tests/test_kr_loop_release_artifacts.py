from __future__ import annotations

import datetime as dt
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tests.test_kr_loop_engineer_mutation import NOW, _bundle, _EditingWorker, _repository
from trading_agent.kr_autonomous_outcome_models import KrLoopFailureCode
from trading_agent.kr_loop_engineer_mutation import KrLoopMutationExecutor, KrLoopMutationStatus
from trading_agent.kr_loop_engineer_policy import mutation_contract
from trading_agent.kr_loop_release_artifacts import (
    InvalidKrLoopReleaseArtifactError,
    KrLoopReleaseArtifactStore,
)


def test_successful_mutation_retains_verified_candidate_and_baseline_sources(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path / "source")
    bundle = _bundle(KrLoopFailureCode.CRITIC_CLUSTER_COUNT)
    changed = mutation_contract(bundle, base).allowed_paths[0]
    artifacts = tmp_path / "artifacts"

    result = KrLoopMutationExecutor(
        repository=repository,
        task_root=tmp_path / "tasks",
        artifact_root=artifacts,
        worker=_EditingWorker(changed),
    ).execute(bundle, base_commit=base, now=NOW)

    assert result.status is KrLoopMutationStatus.COMPLETED
    assert result.snapshot is not None
    manifest = KrLoopReleaseArtifactStore(artifacts).verified(result.snapshot.candidate_id)
    assert manifest.candidate_commit == result.snapshot.candidate_commit
    assert manifest.base_commit == base
    assert manifest.patch_sha256 == result.snapshot.patch_sha256
    assert _git(manifest.candidate_root, "rev-parse", "HEAD") == result.snapshot.candidate_commit
    assert _git(manifest.baseline_root, "rev-parse", "HEAD") == base
    assert "CHANGED = True" in (manifest.candidate_root / changed).read_text(encoding="utf-8")
    assert "CHANGED = True" not in (manifest.baseline_root / changed).read_text(encoding="utf-8")
    assert stat.S_IMODE(manifest.candidate_root.stat().st_mode) == 0o500
    assert stat.S_IMODE(manifest.baseline_root.stat().st_mode) == 0o500
    assert not tuple((tmp_path / "tasks").iterdir())


def test_release_verification_rejects_source_tampering(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path / "source")
    bundle = _bundle(KrLoopFailureCode.CRITIC_CLUSTER_COUNT)
    changed = mutation_contract(bundle, base).allowed_paths[0]
    artifacts = tmp_path / "artifacts"
    result = KrLoopMutationExecutor(
        repository=repository,
        task_root=tmp_path / "tasks",
        artifact_root=artifacts,
        worker=_EditingWorker(changed),
    ).execute(bundle, base_commit=base, now=NOW)
    assert result.snapshot is not None
    manifest = KrLoopReleaseArtifactStore(artifacts).verified(result.snapshot.candidate_id)
    target = manifest.candidate_root / changed
    target.chmod(0o600)
    target.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(InvalidKrLoopReleaseArtifactError):
        _ = KrLoopReleaseArtifactStore(artifacts).verified(result.snapshot.candidate_id)


def test_rejected_mutation_does_not_publish_release(tmp_path: Path) -> None:
    repository, base = _repository(tmp_path / "source")
    bundle = _bundle(KrLoopFailureCode.CRITIC_CLUSTER_COUNT)
    result = KrLoopMutationExecutor(
        repository=repository,
        task_root=tmp_path / "tasks",
        artifact_root=tmp_path / "artifacts",
        worker=_EditingWorker("README.md"),
    ).execute(bundle, base_commit=base, now=dt.datetime.now(dt.UTC))

    assert result.status is KrLoopMutationStatus.REJECTED
    assert not (tmp_path / "artifacts" / "releases").exists()


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repository), *arguments),
        check=True,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )
    return completed.stdout.strip()
