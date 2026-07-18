"""Orchestrate bounded in-place Grok task prepare/run with fail-closed guards."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from development_harness.grok_command import (
    GrokCommandError,
    build_grok_command,
    build_worker_prompt,
    worker_facing_commands,
)
from development_harness.grok_process_env import sanitize_git_routing_environ
from development_harness.grok_verification import GrokVerificationError, run_contract_commands
from development_harness.grok_worker_process import WorkerProcessError, run_worker_process
from development_harness.grok_worker_report import GrokTaskReport, GrokWorkerSummary, parse_worker_summary
from development_harness.grok_workspace_guard import (
    GrokWorkspaceGuardError,
    WorkspaceSnapshot,
    assert_allowed_paths_have_no_symlinks,
    assert_checkout_is_safe,
    assert_main_repository_root,
    assert_not_sparse_checkout,
    capture_workspace_snapshot,
    verify_workspace_snapshot,
)
from development_harness.task_contract import GrokTaskContract

_GIT_TIMEOUT_SECONDS: Final = 30
_GROK_TIMEOUT_SECONDS: Final = 1_800
_MAX_WORKER_STDOUT_BYTES: Final = 1_048_576
_USER_OWNED_PATH_ROOTS: Final = frozenset({".hermes", ".omo", ".hermes/", ".omo/"})

# Stable public re-export for callers/tests that import command builders from the runner.
__all__ = (
    "GrokTaskPlan",
    "GrokTaskRunnerError",
    "assert_changed_paths_allowed",
    "build_grok_command",
    "prepare_grok_task",
    "run_grok_task",
)


class GrokTaskRunnerError(RuntimeError):
    """Raised when a worker task cannot safely proceed."""


@dataclass(frozen=True, slots=True)
class GrokTaskPlan:
    task_id: str
    base_commit: str
    repository: Path
    command: tuple[str, ...]
    prompt: str
    contract: GrokTaskContract
    snapshot: WorkspaceSnapshot


def _run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=False,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        env=sanitize_git_routing_environ(),
    )
    if completed.returncode != 0:
        raise GrokTaskRunnerError("Git preflight failed")
    return completed.stdout


def _is_user_owned_path(path: str) -> bool:
    return path in _USER_OWNED_PATH_ROOTS or path.startswith(".hermes/") or path.startswith(".omo/")


def prepare_grok_task(
    contract: GrokTaskContract,
    *,
    repo: Path,
    grok_binary: str = "grok",
    dry_run: bool,
) -> GrokTaskPlan:
    _ = dry_run
    if type(contract) is not GrokTaskContract:
        raise GrokTaskRunnerError("invalid task contract")
    contract = GrokTaskContract.model_validate(contract.model_dump(mode="python"))
    try:
        repository = assert_main_repository_root(repo)
        # Symlink allow-list checks precede dirty-checkout so protected path
        # problems surface before untracked fixture noise.
        assert_allowed_paths_have_no_symlinks(repository, contract.allowed_paths)
        assert_checkout_is_safe(repository)
        if _run_git(repository, "rev-parse", "HEAD").strip() != contract.base_commit:
            raise GrokTaskRunnerError("task contract base does not match the checkout")
        snapshot = capture_workspace_snapshot(repository)
    except GrokWorkspaceGuardError as error:
        raise GrokTaskRunnerError(str(error)) from error
    try:
        prompt = build_worker_prompt(contract)
    except GrokCommandError as error:
        raise GrokTaskRunnerError(str(error)) from error
    return GrokTaskPlan(
        task_id=contract.task_id,
        base_commit=contract.base_commit,
        repository=repository,
        command=build_grok_command(
            contract, grok_binary=grok_binary, repository=repository, prompt=prompt
        ),
        prompt=prompt,
        contract=contract,
        snapshot=snapshot,
    )


def assert_changed_paths_allowed(changed_paths: tuple[str, ...], allowed_paths: tuple[str, ...]) -> None:
    if any(path not in allowed_paths for path in changed_paths):
        raise GrokTaskRunnerError("worker changed a path outside the contract")


def _nul_paths(output: str) -> tuple[str, ...]:
    return tuple(path for path in output.split("\0") if path)


def _changed_paths(repo: Path, base_commit: str) -> tuple[str, ...]:
    worktree = _nul_paths(_run_git(repo, "diff", "--name-only", "-z", "--no-renames", base_commit))
    index = _nul_paths(
        _run_git(repo, "diff", "--name-only", "-z", "--no-renames", "--cached", base_commit)
    )
    untracked = _nul_paths(_run_git(repo, "ls-files", "-z", "--others", "--exclude-standard"))
    paths = {
        path for path in (*worktree, *index, *untracked) if path and not _is_user_owned_path(path)
    }
    return tuple(sorted(paths))


def _summary_matches_changed_paths(
    summary_files: tuple[str, ...], changed_paths: tuple[str, ...]
) -> bool:
    return len(summary_files) == len(set(summary_files)) and set(summary_files) == set(changed_paths)


def _failed_report(
    plan: GrokTaskPlan,
    *,
    changed_paths: tuple[str, ...],
    worker_exit_code: int | None,
) -> GrokTaskReport:
    return GrokTaskReport(
        schema_version=1,
        task_id=plan.task_id,
        base_commit=plan.base_commit,
        status="worker_failed",
        changed_paths=changed_paths,
        worker_exit_code=worker_exit_code,
        summary=None,
    )


def _revalidate_launch_state(plan: GrokTaskPlan) -> None:
    """Re-check clean snapshot and repository root immediately before launch."""

    try:
        repository = assert_main_repository_root(plan.repository)
        if repository != plan.repository:
            raise GrokTaskRunnerError("repository root changed before worker launch")
        assert_allowed_paths_have_no_symlinks(plan.repository, plan.contract.allowed_paths)
        assert_checkout_is_safe(plan.repository)
        if _run_git(plan.repository, "rev-parse", "HEAD").strip() != plan.base_commit:
            raise GrokTaskRunnerError("task contract base does not match the checkout")
        # Exact empty-dir inventory match before launch (no allowed-parent delta).
        verify_workspace_snapshot(plan.repository, plan.snapshot)
    except GrokWorkspaceGuardError as error:
        raise GrokTaskRunnerError(str(error)) from error


def _post_workspace_validation(plan: GrokTaskPlan) -> tuple[str, ...]:
    """Validate topology, snapshot, sparse-checkout, and allow-listed changed paths."""

    try:
        # Revalidate symlink/.git topology before any post-worker Git inventory.
        repository = assert_main_repository_root(plan.repository)
        if repository != plan.repository:
            raise GrokTaskRunnerError("repository root changed under the worker")
        verify_workspace_snapshot(
            plan.repository,
            plan.snapshot,
            allowed_paths=plan.contract.allowed_paths,
        )
        assert_not_sparse_checkout(plan.repository)
        assert_allowed_paths_have_no_symlinks(plan.repository, plan.contract.allowed_paths)
    except GrokWorkspaceGuardError as error:
        raise GrokTaskRunnerError(str(error)) from error
    changed_paths = _changed_paths(plan.repository, plan.base_commit)
    assert_changed_paths_allowed(changed_paths, plan.contract.allowed_paths)
    return changed_paths


def _post_worker_paths(plan: GrokTaskPlan) -> tuple[str, ...]:
    return _post_workspace_validation(plan)


def _run_contract_verification(plan: GrokTaskPlan) -> bool:
    commands = (*plan.contract.required_commands, *plan.contract.manual_qa_commands)
    try:
        return run_contract_commands(commands, cwd=plan.repository)
    except GrokVerificationError:
        return False


def _parse_summary(plan: GrokTaskPlan, stdout: str) -> GrokWorkerSummary | None:
    try:
        required = frozenset(
            (
                *worker_facing_commands(plan.contract.required_commands),
                *worker_facing_commands(plan.contract.manual_qa_commands),
            )
        )
    except GrokCommandError:
        return None
    return parse_worker_summary(
        stdout,
        allowed_paths=frozenset(plan.contract.allowed_paths),
        required_verification=required,
    )


def run_grok_task(plan: GrokTaskPlan, *, dry_run: bool) -> GrokTaskReport:
    if dry_run:
        return GrokTaskReport(
            schema_version=1,
            task_id=plan.task_id,
            base_commit=plan.base_commit,
            status="planned",
            changed_paths=(),
            worker_exit_code=None,
            summary=None,
        )

    _revalidate_launch_state(plan)

    worker_exit_code: int | None
    stdout_text = ""
    try:
        result = run_worker_process(
            plan.command,
            cwd=plan.repository,
            timeout_seconds=_GROK_TIMEOUT_SECONDS,
            max_stdout_bytes=_MAX_WORKER_STDOUT_BYTES,
        )
        worker_exit_code = result.returncode
        stdout_text = result.stdout.decode("utf-8", errors="replace")
    except (OSError, TimeoutError, WorkerProcessError):
        return _failed_report(plan, changed_paths=_post_worker_paths(plan), worker_exit_code=None)

    changed_paths = _post_worker_paths(plan)
    summary = _parse_summary(plan, stdout_text)
    if (
        worker_exit_code != 0
        or summary is None
        or not _summary_matches_changed_paths(summary.changed_files, changed_paths)
    ):
        return _failed_report(plan, changed_paths=changed_paths, worker_exit_code=worker_exit_code)
    verification_ok = _run_contract_verification(plan)
    # Always re-validate after independent verification success, nonzero, timeout,
    # or side effect so verification cannot hide workspace damage.
    changed_paths = _post_workspace_validation(plan)
    if not verification_ok:
        return _failed_report(plan, changed_paths=changed_paths, worker_exit_code=worker_exit_code)
    if not _summary_matches_changed_paths(summary.changed_files, changed_paths):
        return _failed_report(plan, changed_paths=changed_paths, worker_exit_code=worker_exit_code)
    return GrokTaskReport(
        schema_version=1,
        task_id=plan.task_id,
        base_commit=plan.base_commit,
        status="completed",
        changed_paths=changed_paths,
        worker_exit_code=worker_exit_code,
        summary=summary,
    )
