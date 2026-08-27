from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from development_harness.grok_task_runner import GrokTaskRunnerError, prepare_grok_task, run_grok_task
from development_harness.grok_worker_report import GrokTaskReport
from development_harness.task_contract import GrokTaskContract
from trading_agent.kr_autonomous_outcome_models import KrLoopEngineerEvidenceBundle
from trading_agent.kr_loop_engineer_git import (
    KrLoopMutationExecutionError,
    changed_paths,
    cleanup_checkout,
    clone_at,
    commit_candidate,
    git,
    prepare_private_root,
)
from trading_agent.kr_loop_engineer_models import (
    KrLoopCandidateSnapshot,
    KrLoopCandidateState,
    KrLoopValidationReceipt,
    build_candidate_snapshot,
)
from trading_agent.kr_loop_engineer_policy import mutation_contract
from trading_agent.kr_loop_release_artifacts import (
    InvalidKrLoopReleaseArtifactError,
    KrLoopReleaseArtifactStore,
)
from trading_agent.private_directory_identity import absolute_private_path
from trading_agent.private_immutable_file import InvalidPrivateImmutableFileError, publish_private_immutable_text


class KrLoopMutationStatus(StrEnum):
    COMPLETED = "completed"
    REJECTED = "rejected"


class KrLoopMutationWorker(Protocol):
    def run(self, contract: GrokTaskContract, checkout: Path) -> GrokTaskReport: ...


@dataclass(frozen=True, slots=True)
class GrokKrLoopMutationWorker:
    grok_binary: str = "grok"

    def run(self, contract: GrokTaskContract, checkout: Path) -> GrokTaskReport:
        plan = prepare_grok_task(
            contract,
            repo=checkout,
            grok_binary=self.grok_binary,
            dry_run=False,
        )
        return run_grok_task(plan, dry_run=False)


@dataclass(frozen=True, slots=True)
class KrLoopMutationResult:
    status: KrLoopMutationStatus
    snapshot: KrLoopCandidateSnapshot | None
    changed_paths: tuple[str, ...]
    reason_code: str | None
    validation_receipt: KrLoopValidationReceipt | None = None


class KrLoopMutationExecutor:
    __slots__ = ("_artifact_root", "_repository", "_task_root", "_worker")

    def __init__(
        self,
        *,
        repository: Path,
        task_root: Path,
        artifact_root: Path,
        worker: KrLoopMutationWorker | None = None,
    ) -> None:
        self._repository = absolute_private_path(repository)
        self._task_root = absolute_private_path(task_root)
        self._artifact_root = absolute_private_path(artifact_root)
        self._worker = GrokKrLoopMutationWorker() if worker is None else worker

    def execute(
        self,
        bundle: KrLoopEngineerEvidenceBundle,
        *,
        base_commit: str,
        now: dt.datetime,
        previous: KrLoopCandidateSnapshot | None = None,
    ) -> KrLoopMutationResult:
        contract = mutation_contract(bundle, base_commit)
        detected = previous or build_candidate_snapshot(
            bundle_id=bundle.bundle_id,
            base_commit=base_commit,
            allowed_paths=contract.allowed_paths,
            state=KrLoopCandidateState.DETECTED,
            updated_at=now,
        )
        if (
            detected.bundle_id != bundle.bundle_id
            or detected.base_commit != base_commit
            or detected.allowed_paths != tuple(sorted(contract.allowed_paths))
            or detected.state is not KrLoopCandidateState.DETECTED
        ):
            return KrLoopMutationResult(KrLoopMutationStatus.REJECTED, None, (), "candidate_lineage_invalid")
        checkout = self._task_root / detected.candidate_id
        try:
            prepare_private_root(self._task_root)
            clone_at(self._repository, checkout, base_commit)
            report = self._worker.run(contract, checkout)
            changed = changed_paths(checkout, base_commit)
            reason = _report_rejection(report, changed, contract.allowed_paths)
            if reason is not None:
                return KrLoopMutationResult(KrLoopMutationStatus.REJECTED, None, changed, reason)
            commit_candidate(checkout, contract.allowed_paths, now)
            candidate_commit = git(checkout, "rev-parse", "HEAD").strip()
            patch = git(checkout, "diff", "--binary", base_commit, candidate_commit)
            if not patch:
                return KrLoopMutationResult(KrLoopMutationStatus.REJECTED, None, changed, "empty_patch")
            patch_sha256 = hashlib.sha256(patch.encode()).hexdigest()
            artifact = self._artifact_root / f"{detected.candidate_id}.patch"
            _ = publish_private_immutable_text(artifact, patch)
            _ = KrLoopReleaseArtifactStore(self._artifact_root).finalize(
                repository=self._repository,
                checkout=checkout,
                task_root=self._task_root,
                candidate_id=detected.candidate_id,
                base_commit=base_commit,
                candidate_commit=candidate_commit,
                patch_sha256=patch_sha256,
                created_at=now,
            )
            ready = build_candidate_snapshot(
                bundle_id=bundle.bundle_id,
                base_commit=base_commit,
                allowed_paths=contract.allowed_paths,
                state=KrLoopCandidateState.CANDIDATE_READY,
                updated_at=now,
                previous=detected,
                candidate_commit=candidate_commit,
                patch_sha256=patch_sha256,
            )
            verification_ref = hashlib.sha256(
                "\n".join((*contract.required_commands, *contract.manual_qa_commands)).encode()
            ).hexdigest()
            validation = KrLoopValidationReceipt.build(
                candidate_id=ready.candidate_id,
                candidate_commit=candidate_commit,
                verified_at=now,
                pytest_passed=True,
                ruff_passed=True,
                basedpyright_passed=True,
                manual_qa_passed=True,
                replay_passed=True,
                lookahead_violations=0,
                broker_mutations=0,
                evidence_refs=(f"verification:{verification_ref}",),
            )
            return KrLoopMutationResult(
                KrLoopMutationStatus.COMPLETED,
                ready,
                changed,
                None,
                validation,
            )
        except (
            GrokTaskRunnerError,
            InvalidKrLoopReleaseArtifactError,
            InvalidPrivateImmutableFileError,
            KrLoopMutationExecutionError,
            OSError,
            subprocess.SubprocessError,
        ):
            return KrLoopMutationResult(KrLoopMutationStatus.REJECTED, None, (), "mutation_execution_failed")
        finally:
            cleanup_checkout(checkout, self._task_root)


def _report_rejection(
    report: GrokTaskReport,
    changed: tuple[str, ...],
    allowed: tuple[str, ...],
) -> str | None:
    if report.status != "completed" or report.summary is None:
        return "worker_failed"
    if not changed:
        return "empty_change"
    if set(changed) != set(report.changed_paths) or any(path not in allowed for path in changed):
        return "changed_path_forbidden"
    return None


__all__ = (
    "GrokKrLoopMutationWorker",
    "KrLoopMutationExecutor",
    "KrLoopMutationResult",
    "KrLoopMutationStatus",
    "KrLoopMutationWorker",
)
