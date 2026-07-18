from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from development_harness.grok_worker_report import (
    WORKER_SUMMARY_JSON_SCHEMA,
    GrokTaskReport,
    parse_worker_summary,
)
from development_harness.task_contract import GrokTaskContract

_GIT_TIMEOUT_SECONDS: Final = 30
_GROK_TIMEOUT_SECONDS: Final = 1_800
_USER_OWNED_STATUS_ENTRIES: Final = frozenset({"?? .hermes/", "?? .omo/"})
_USER_OWNED_PATH_ROOTS: Final = frozenset({".hermes", ".omo", ".hermes/", ".omo/"})


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


def _run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(repo), *args),
        check=False,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise GrokTaskRunnerError("Git preflight failed")
    return completed.stdout


def _repository_root(repo: Path) -> Path:
    resolved = repo.resolve(strict=True)
    root = Path(_run_git(resolved, "rev-parse", "--show-toplevel").strip()).resolve(strict=True)
    if root != resolved:
        raise GrokTaskRunnerError("task runner requires the repository root")
    return root


def _status_entries(repo: Path) -> tuple[str, ...]:
    return tuple(entry for entry in _run_git(repo, "status", "--porcelain=v1", "-z").split("\0") if entry)


def _assert_checkout_is_safe(status_entries: tuple[str, ...]) -> None:
    if status_entries and not set(status_entries).issubset(_USER_OWNED_STATUS_ENTRIES):
        raise GrokTaskRunnerError("checkout contains changes outside the approved user-owned state")


def _is_user_owned_path(path: str) -> bool:
    return path in _USER_OWNED_PATH_ROOTS or path.startswith(".hermes/") or path.startswith(".omo/")


def _bullet_block(values: tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in values)


def _build_prompt(contract: GrokTaskContract) -> str:
    fields = ", ".join(contract.expected_summary_fields)
    return (
        "Implement exactly this bounded development task.\n"
        f"Task ID: {contract.task_id}\n"
        f"Objective: {contract.objective}\n\n"
        f"Allowed paths:\n{_bullet_block(contract.allowed_paths)}\n\n"
        f"Required verification commands:\n{_bullet_block(contract.required_commands)}\n\n"
        f"Manual QA commands:\n{_bullet_block(contract.manual_qa_commands)}\n\n"
        "Rules: use TDD; do not change paths outside the allow-list; do not read credentials, "
        "provider modules, broker modules, or user-owned .hermes/.omo state; do not make network, "
        "market-data, broker, Paper, or live-trading calls; do not commit, push, create a branch, "
        "create a worktree, or spawn a subagent. Work in-place on the current repository root only. "
        "You may edit allow-listed working-tree files but must not commit or push history. "
        "Run the required verification. Your final response must be JSON only and contain these keys: "
        f"{fields}.\n"
    )


def build_grok_command(
    contract: GrokTaskContract,
    *,
    grok_binary: str,
    repository: Path,
    prompt: str,
) -> tuple[str, ...]:
    return (
        grok_binary,
        "--cwd",
        str(repository),
        "--always-approve",
        "--permission-mode",
        "bypassPermissions",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--json-schema",
        WORKER_SUMMARY_JSON_SCHEMA,
        "--no-plan",
        "--no-subagents",
        "--disable-web-search",
        "--no-memory",
        "--max-turns",
        str(contract.max_turns),
    )


def prepare_grok_task(
    contract: GrokTaskContract,
    *,
    repo: Path,
    grok_binary: str = "grok",
    dry_run: bool,
) -> GrokTaskPlan:
    if type(contract) is not GrokTaskContract:
        raise GrokTaskRunnerError("invalid task contract")
    contract = GrokTaskContract.model_validate(contract.model_dump(mode="python"))
    repository = _repository_root(repo)
    if _run_git(repository, "rev-parse", "HEAD").strip() != contract.base_commit:
        raise GrokTaskRunnerError("task contract base does not match the checkout")
    _assert_checkout_is_safe(_status_entries(repository))
    prompt = _build_prompt(contract)
    return GrokTaskPlan(
        task_id=contract.task_id,
        base_commit=contract.base_commit,
        repository=repository,
        command=build_grok_command(
            contract, grok_binary=grok_binary, repository=repository, prompt=prompt
        ),
        prompt=prompt,
        contract=contract,
    )


def assert_changed_paths_allowed(changed_paths: tuple[str, ...], allowed_paths: tuple[str, ...]) -> None:
    if any(path not in allowed_paths for path in changed_paths):
        raise GrokTaskRunnerError("worker changed a path outside the contract")


def _assert_head_unchanged(repo: Path, base_commit: str) -> None:
    if _run_git(repo, "rev-parse", "HEAD").strip() != base_commit:
        raise GrokTaskRunnerError("worker committed changes; HEAD no longer matches the contract base")


def _nul_paths(output: str) -> tuple[str, ...]:
    return tuple(path for path in output.split("\0") if path)


def _changed_paths(repo: Path, base_commit: str) -> tuple[str, ...]:
    # Disable rename detection so moves surface as both the old and new paths.
    worktree = _nul_paths(_run_git(repo, "diff", "--name-only", "-z", "--no-renames", base_commit))
    index = _nul_paths(_run_git(repo, "diff", "--name-only", "-z", "--no-renames", "--cached", base_commit))
    untracked = _nul_paths(_run_git(repo, "ls-files", "-z", "--others", "--exclude-standard"))
    paths = {path for path in (*worktree, *index, *untracked) if path and not _is_user_owned_path(path)}
    return tuple(sorted(paths))


def _summary_matches_changed_paths(summary_files: tuple[str, ...], changed_paths: tuple[str, ...]) -> bool:
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


def _post_worker_paths(plan: GrokTaskPlan) -> tuple[str, ...]:
    _assert_head_unchanged(plan.repository, plan.base_commit)
    changed_paths = _changed_paths(plan.repository, plan.base_commit)
    assert_changed_paths_allowed(changed_paths, plan.contract.allowed_paths)
    return changed_paths


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

    try:
        completed = subprocess.run(
            plan.command,
            cwd=plan.repository,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GROK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _failed_report(plan, changed_paths=_post_worker_paths(plan), worker_exit_code=None)

    changed_paths = _post_worker_paths(plan)
    summary = parse_worker_summary(completed.stdout)
    if (
        completed.returncode != 0
        or summary is None
        or not _summary_matches_changed_paths(summary.changed_files, changed_paths)
    ):
        return _failed_report(plan, changed_paths=changed_paths, worker_exit_code=completed.returncode)
    return GrokTaskReport(
        schema_version=1,
        task_id=plan.task_id,
        base_commit=plan.base_commit,
        status="completed",
        changed_paths=changed_paths,
        worker_exit_code=completed.returncode,
        summary=summary,
    )
