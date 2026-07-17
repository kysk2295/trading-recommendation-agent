from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from development_harness.task_contract import GrokTaskContract

_GIT_TIMEOUT_SECONDS: Final = 30
_GROK_TIMEOUT_SECONDS: Final = 1_800
_USER_OWNED_STATUS: Final = "?? .hermes/"


class GrokTaskRunnerError(RuntimeError):
    """Raised when a worker task cannot safely proceed."""


@dataclass(frozen=True)
class GrokTaskPlan:
    task_id: str
    base_commit: str
    branch_name: str
    repository: Path
    worktree_path: Path
    command: tuple[str, ...]
    prompt: str
    contract: GrokTaskContract


@dataclass(frozen=True)
class GrokTaskReport:
    schema_version: Literal[1]
    task_id: str
    base_commit: str
    status: Literal["planned", "completed", "worker_failed"]
    worktree_id: str
    changed_paths: tuple[str, ...]
    worker_exit_code: int | None

    def as_safe_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "base_commit": self.base_commit,
            "status": self.status,
            "worktree_id": self.worktree_id,
            "changed_paths": self.changed_paths,
            "worker_exit_code": self.worker_exit_code,
        }


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


def _git_return_code(repo: Path, *args: str) -> int:
    return subprocess.run(
        ("git", "-C", str(repo), *args),
        check=False,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    ).returncode


def _repository_root(repo: Path) -> Path:
    resolved = repo.resolve(strict=True)
    root = Path(_run_git(resolved, "rev-parse", "--show-toplevel").strip()).resolve(strict=True)
    if root != resolved:
        raise GrokTaskRunnerError("task runner requires the repository root")
    return root


def _status_entries(repo: Path) -> tuple[str, ...]:
    output = _run_git(repo, "status", "--porcelain=v1", "-z")
    return tuple(entry for entry in output.split("\0") if entry)


def _assert_checkout_is_safe(status_entries: tuple[str, ...]) -> None:
    if not status_entries or status_entries == (_USER_OWNED_STATUS,):
        return
    raise GrokTaskRunnerError("checkout contains changes outside the approved user-owned state")


def _build_prompt(contract: GrokTaskContract) -> str:
    paths = "\n".join(f"- {path}" for path in contract.allowed_paths)
    commands = "\n".join(f"- {command}" for command in contract.required_commands)
    manual_qa = "\n".join(f"- {command}" for command in contract.manual_qa_commands)
    fields = ", ".join(contract.expected_summary_fields)
    return (
        "Implement exactly this bounded development task.\n"
        f"Task ID: {contract.task_id}\n"
        f"Objective: {contract.objective}\n\n"
        "Allowed paths:\n"
        f"{paths}\n\n"
        "Required verification commands:\n"
        f"{commands}\n\n"
        "Manual QA commands:\n"
        f"{manual_qa}\n\n"
        "Rules: use TDD; do not change paths outside the allow-list; do not read credentials or "
        "user-owned .hermes; do not make network, market-data, broker, Paper, or live-trading calls; "
        "do not push, remove a worktree, or create a subagent. Run the required verification. "
        "Your final response must be JSON only and contain these keys: "
        f"{fields}.\n"
    )


def build_grok_command(
    contract: GrokTaskContract,
    *,
    grok_binary: str,
    worktree: Path,
    prompt: str,
) -> tuple[str, ...]:
    command: list[str] = [
        grok_binary,
        "--cwd",
        str(worktree),
        "-p",
        prompt,
        "--output-format",
        "json",
        "--max-turns",
        str(contract.max_turns),
        "--no-subagents",
        "--disable-web-search",
        "--sandbox",
        "strict",
        "--permission-mode",
        "acceptEdits",
        "--allow",
        "Read(**)",
        "--allow",
        "Grep(**)",
        "--allow",
        "Bash(git status*)",
        "--allow",
        "Bash(git diff*)",
        "--allow",
        "Bash(git add *)",
        "--allow",
        "Bash(git commit *)",
        "--allow",
        "Bash(git rev-parse *)",
        "--allow",
        "Bash(*)",
        "--allow",
        "Bash(ls *)",
        "--allow",
        "Bash(find *)",
        "--allow",
        "Bash(pwd)",
        "--allow",
        "Bash(rg *)",
        "--allow",
        "Bash(sed *)",
        "--allow",
        "Bash(head *)",
        "--allow",
        "Bash(tail *)",
        "--allow",
        "Bash(wc *)",
        "--allow",
        "Bash(cat CODEX_START_HERE.md*)",
        "--deny",
        "Read(.hermes/**)",
        "--deny",
        "Edit(.hermes/**)",
        "--deny",
        "Write(.hermes/**)",
        "--deny",
        "Bash(git push*)",
        "--deny",
        "Bash(git worktree*)",
        "--deny",
        "Bash(curl *)",
        "--deny",
        "Bash(wget *)",
        "--deny",
        "Bash(nc *)",
        "--deny",
        "Bash(ssh *)",
        "--deny",
        "Bash(scp *)",
        "--deny",
        "Bash(rm *)",
    ]
    for path in contract.allowed_paths:
        command.extend(("--allow", f"Edit({path})", "--allow", f"Write({path})"))
    for command_to_run in (*contract.required_commands, *contract.manual_qa_commands):
        command.extend(("--allow", f"Bash({command_to_run})"))
    return tuple(command)


def prepare_grok_task(
    contract: GrokTaskContract,
    *,
    repo: Path,
    worktree_root: Path,
    grok_binary: str = "grok",
    dry_run: bool,
) -> GrokTaskPlan:
    if type(contract) is not GrokTaskContract:
        raise GrokTaskRunnerError("invalid task contract")
    contract = GrokTaskContract.model_validate(contract.model_dump(mode="python"))
    repository = _repository_root(repo)
    head = _run_git(repository, "rev-parse", "HEAD").strip()
    if head != contract.base_commit:
        raise GrokTaskRunnerError("task contract base does not match the checkout")
    _assert_checkout_is_safe(_status_entries(repository))

    root = worktree_root.resolve(strict=False)
    worktree_path = root / contract.task_id
    branch_name = f"grok/{contract.task_id}"
    if worktree_path.exists() or worktree_path.is_symlink():
        raise GrokTaskRunnerError("worktree destination already exists")
    branch_status = _git_return_code(repository, "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}")
    if branch_status == 0:
        raise GrokTaskRunnerError("worker branch already exists")
    if branch_status != 1:
        raise GrokTaskRunnerError("Git preflight failed")

    prompt = _build_prompt(contract)
    return GrokTaskPlan(
        task_id=contract.task_id,
        base_commit=contract.base_commit,
        branch_name=branch_name,
        repository=repository,
        worktree_path=worktree_path,
        command=build_grok_command(contract, grok_binary=grok_binary, worktree=worktree_path, prompt=prompt),
        prompt=prompt,
        contract=contract,
    )


def assert_changed_paths_allowed(changed_paths: tuple[str, ...], allowed_paths: tuple[str, ...]) -> None:
    if any(path not in allowed_paths for path in changed_paths):
        raise GrokTaskRunnerError("worker changed a path outside the contract")


def _changed_paths(worktree: Path, base_commit: str) -> tuple[str, ...]:
    committed = _run_git(worktree, "diff", "--name-only", "-z", f"{base_commit}...HEAD")
    working = _run_git(worktree, "status", "--porcelain=v1", "-z")
    paths: set[str] = {path for path in committed.split("\0") if path}
    for entry in working.split("\0"):
        if entry:
            paths.add(entry[3:])
    return tuple(sorted(paths))


def _has_expected_summary(raw_stdout: str, expected_fields: tuple[str, ...]) -> bool:
    try:
        outer = json.loads(raw_stdout)
        text = outer["text"]
        summary = json.loads(text)
    except (KeyError, TypeError, json.JSONDecodeError):
        return False
    return isinstance(summary, dict) and all(field in summary for field in expected_fields)


def run_grok_task(plan: GrokTaskPlan, *, dry_run: bool) -> GrokTaskReport:
    if dry_run:
        return GrokTaskReport(
            schema_version=1,
            task_id=plan.task_id,
            base_commit=plan.base_commit,
            status="planned",
            worktree_id=plan.task_id,
            changed_paths=(),
            worker_exit_code=None,
        )

    plan.worktree_path.parent.mkdir(parents=True, exist_ok=True)
    added = subprocess.run(
        (
            "git",
            "-C",
            str(plan.repository),
            "worktree",
            "add",
            "-b",
            plan.branch_name,
            str(plan.worktree_path),
            plan.base_commit,
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    if added.returncode != 0:
        raise GrokTaskRunnerError("could not create worker worktree")

    try:
        completed = subprocess.run(
            plan.command,
            cwd=plan.worktree_path,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GROK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return GrokTaskReport(
            schema_version=1,
            task_id=plan.task_id,
            base_commit=plan.base_commit,
            status="worker_failed",
            worktree_id=plan.task_id,
            changed_paths=_changed_paths(plan.worktree_path, plan.base_commit),
            worker_exit_code=None,
        )
    changed_paths = _changed_paths(plan.worktree_path, plan.base_commit)
    assert_changed_paths_allowed(changed_paths, plan.contract.allowed_paths)
    if completed.returncode != 0 or not _has_expected_summary(completed.stdout, plan.contract.expected_summary_fields):
        return GrokTaskReport(
            schema_version=1,
            task_id=plan.task_id,
            base_commit=plan.base_commit,
            status="worker_failed",
            worktree_id=plan.task_id,
            changed_paths=changed_paths,
            worker_exit_code=completed.returncode,
        )
    return GrokTaskReport(
        schema_version=1,
        task_id=plan.task_id,
        base_commit=plan.base_commit,
        status="completed",
        worktree_id=plan.task_id,
        changed_paths=changed_paths,
        worker_exit_code=completed.returncode,
    )
