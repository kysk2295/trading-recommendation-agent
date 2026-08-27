from __future__ import annotations

import datetime as dt
import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from development_harness.grok_worker_report import GrokTaskReport, GrokWorkerSummary
from development_harness.task_contract import GrokTaskContract
from trading_agent.kr_autonomous_outcome_models import KrLoopEngineerEvidenceBundle, KrLoopFailureCode
from trading_agent.kr_loop_engineer_models import KrLoopCandidateState
from trading_agent.kr_loop_engineer_mutation import KrLoopMutationExecutor, KrLoopMutationStatus
from trading_agent.kr_loop_engineer_policy import mutation_contract

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 27, 18, 0, tzinfo=KST)


@dataclass(frozen=True, slots=True)
class _EditingWorker:
    changed_path: str

    def run(self, contract: GrokTaskContract, checkout: Path) -> GrokTaskReport:
        target = checkout / self.changed_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{target.read_text(encoding='utf-8')}\nCHANGED = True\n", encoding="utf-8")
        summary = GrokWorkerSummary(
            changed_files=(self.changed_path,),
            verification=(*contract.required_commands, *contract.manual_qa_commands),
            concerns=(),
        )
        return GrokTaskReport(
            schema_version=1,
            task_id=contract.task_id,
            base_commit=contract.base_commit,
            status="completed",
            changed_paths=(self.changed_path,),
            worker_exit_code=0,
            summary=summary,
        )


def test_failure_policy_never_allows_host_safety_or_provider_paths() -> None:
    # Given: every supported repeated-failure category.
    forbidden = ("alpaca", "kis_", "ls_", "credential", "risk", "paper_mutation", "AGENTS.md")

    # When: the host derives each bounded coding contract.
    contracts = tuple(mutation_contract(_bundle(code), "a" * 40) for code in KrLoopFailureCode)

    # Then: all paths exist in the approved KR reasoning surface and exclude immutable host policy.
    assert all(contract.allowed_paths for contract in contracts)
    assert all(
        not any(marker in path for marker in forbidden) for contract in contracts for path in contract.allowed_paths
    )
    assert all(contract.base_commit == "a" * 40 for contract in contracts)


def test_isolated_mutation_publishes_private_immutable_patch(tmp_path: Path) -> None:
    # Given: a clean local source repository and a worker that changes one allow-listed file.
    repository, base = _repository(tmp_path / "source")
    bundle = _bundle(KrLoopFailureCode.CRITIC_CLUSTER_COUNT)
    contract = mutation_contract(bundle, base)
    changed = contract.allowed_paths[0]
    executor = KrLoopMutationExecutor(
        repository=repository,
        task_root=tmp_path / "tasks",
        artifact_root=tmp_path / "artifacts",
        worker=_EditingWorker(changed),
    )

    # When: the host runs the candidate mutation.
    result = executor.execute(bundle, base_commit=base, now=NOW)

    # Then: the source stays unchanged and a private patch identifies a candidate commit.
    assert result.status is KrLoopMutationStatus.COMPLETED
    assert result.snapshot is not None
    assert result.snapshot.state is KrLoopCandidateState.CANDIDATE_READY
    assert result.snapshot.candidate_commit != base
    assert result.snapshot.patch_sha256 is not None
    assert result.changed_paths == (changed,)
    artifact = tmp_path / "artifacts" / f"{result.snapshot.candidate_id}.patch"
    metadata = artifact.stat()
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert metadata.st_uid == os.getuid()
    assert "CHANGED" not in (repository / changed).read_text(encoding="utf-8")
    assert not tuple((tmp_path / "tasks").iterdir())


def test_out_of_scope_worker_change_is_rejected_without_artifact(tmp_path: Path) -> None:
    # Given: a worker that edits a path outside the fixed host policy.
    repository, base = _repository(tmp_path / "source")
    bundle = _bundle(KrLoopFailureCode.MARKET_DATA)
    executor = KrLoopMutationExecutor(
        repository=repository,
        task_root=tmp_path / "tasks",
        artifact_root=tmp_path / "artifacts",
        worker=_EditingWorker("README.md"),
    )

    # When: mutation output is reconciled against the checkout.
    result = executor.execute(bundle, base_commit=base, now=NOW)

    # Then: the candidate fails closed and publishes no patch.
    assert result.status is KrLoopMutationStatus.REJECTED
    assert result.reason_code == "changed_path_forbidden"
    assert result.snapshot is None
    assert not (tmp_path / "artifacts").exists()


def _repository(path: Path) -> tuple[Path, str]:
    path.mkdir(parents=True)
    for relative in {
        path for code in KrLoopFailureCode for path in mutation_contract(_bundle(code), "a" * 40).allowed_paths
    }:
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("BASE = True\n", encoding="utf-8")
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(path, "init", "-b", "main")
    _git(path, "add", ".")
    _git(path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "base")
    return path, _git(path, "rev-parse", "HEAD").strip()


def _git(path: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(path), *arguments),
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout


def _bundle(code: KrLoopFailureCode) -> KrLoopEngineerEvidenceBundle:
    draft = KrLoopEngineerEvidenceBundle.model_construct(
        bundle_id="",
        failure_code=code,
        subject_ref="symbol:005930",
        source_memory_ids=("1" * 64, "2" * 64, "3" * 64),
        source_task_ids=("4" * 64, "5" * 64, "6" * 64),
        evidence_refs=("evidence:1",),
        change_hypothesis="Tighten the repeated failure boundary with deterministic evidence.",
        created_at=NOW,
    )
    from trading_agent.kr_autonomous_outcome_models import kr_loop_engineer_bundle_id

    return KrLoopEngineerEvidenceBundle.model_validate(
        draft.model_copy(update={"bundle_id": kr_loop_engineer_bundle_id(draft)}).model_dump(mode="python")
    )
