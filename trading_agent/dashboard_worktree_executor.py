from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from trading_agent.dashboard_agent_family import AGENT_FAMILY_REGISTRY
from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1
from trading_agent.dashboard_execution_sandbox import AutonomousExecutionSandbox
from trading_agent.dashboard_outbound_redaction import redact_outbound_text

ExecutionState = Literal["completed", "failed", "uncertain"]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    state: ExecutionState
    result_summary: str
    result_sha256: str | None
    evidence_sha256: tuple[str, ...]
    cleanup_completed: bool
    process_started: bool
    worktree_clean: bool


class AutonomousTaskExecutor(Protocol):
    def preflight(self, trigger: AutonomousTriggerV1) -> str | None: ...

    def execute(self, trigger: AutonomousTriggerV1, task_id: str) -> ExecutionResult: ...


class IsolatedWorktreeExecutor:
    def __init__(
        self,
        *,
        repository: Path,
        environment_root: Path,
        source_evidence_root: Path,
        hermes_executable: Path,
        fixture_mode: bool = False,
        allowed_tool_executables: tuple[Path, ...] = (),
    ) -> None:
        self._repository = repository.resolve()
        self._environment_root = environment_root.resolve()
        self._hermes = hermes_executable
        self._sandbox = AutonomousExecutionSandbox(
            repository=self._repository,
            source_evidence_root=source_evidence_root.resolve(strict=False),
            hermes_executable=self._hermes,
            fixture_mode=fixture_mode,
            allowed_tool_executables=allowed_tool_executables,
        )

    def preflight(self, trigger: AutonomousTriggerV1) -> str | None:
        return self._sandbox.blocker(trigger)

    def execute(self, trigger: AutonomousTriggerV1, task_id: str) -> ExecutionResult:
        task_root = self._environment_root / task_id
        worktree = task_root / "worktree"
        experiment = task_root / "experiment"
        process_started = False
        worktree_added = False
        result: ExecutionResult
        try:
            task_root.mkdir(mode=0o700, parents=True, exist_ok=False)
            experiment.mkdir(mode=0o700)
            added = subprocess.run(
                (
                    "git",
                    "-C",
                    str(self._repository),
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree),
                    trigger.environment_spec.pinned_code_sha,
                ),
                check=False,
                capture_output=True,
                timeout=60,
            )
            if added.returncode != 0:
                result = self._failed("isolated_worktree_setup_failed", cleanup=False)
            else:
                worktree_added = True
                environment = self._sandbox.environment(trigger, experiment)
                command = self._sandbox.argv(self._argv(trigger), task_root, worktree)
                completed = subprocess.run(
                    command,
                    cwd=worktree,
                    env=environment,
                    check=False,
                    capture_output=True,
                    timeout=trigger.budget_envelope.max_runtime_seconds,
                )
                process_started = True
                clean = self._is_clean(worktree)
                stdout = completed.stdout[:64 * 1024]
                result_hash = hashlib.sha256(stdout).hexdigest() if stdout else None
                if completed.returncode != 0 or not stdout or not clean:
                    result = ExecutionResult(
                        state="failed",
                        result_summary=redact_outbound_text(
                            "autonomous process failed" if clean else "isolated worktree became dirty"
                        ),
                        result_sha256=result_hash,
                        evidence_sha256=(),
                        cleanup_completed=False,
                        process_started=True,
                        worktree_clean=clean,
                    )
                else:
                    result = ExecutionResult(
                        state="completed",
                        result_summary=redact_outbound_text(stdout.decode("utf-8", errors="replace").strip()),
                        result_sha256=result_hash,
                        evidence_sha256=self._experiment_hashes(experiment),
                        cleanup_completed=False,
                        process_started=True,
                        worktree_clean=True,
                    )
        except subprocess.TimeoutExpired:
            process_started = True
            result = self._failed("autonomous_process_timeout", cleanup=False, process_started=True)
        except OSError:
            result = self._failed(
                "autonomous_process_start_failed",
                cleanup=False,
                process_started=process_started,
            )
        finally:
            cleanup = self._cleanup(task_root, worktree, worktree_added)
        return ExecutionResult(
            state=result.state,
            result_summary=result.result_summary,
            result_sha256=result.result_sha256,
            evidence_sha256=result.evidence_sha256,
            cleanup_completed=cleanup,
            process_started=result.process_started,
            worktree_clean=result.worktree_clean,
        )

    def _argv(self, trigger: AutonomousTriggerV1) -> tuple[str, ...]:
        role = next(item.role for item in AGENT_FAMILY_REGISTRY if item.family_id == trigger.agent_family_id)
        prompt = (
            f"Role: {role}. Family identity: {trigger.agent_family_id}. "
            f"Memory namespace: research-family:{trigger.agent_family_id}:memory-v1. "
            "This is a separate autonomous task session; never resume an interactive session. "
            "Read only source-bound evidence, write candidate evidence only in the declared experiment root, "
            "and do not mutate providers, Paper state, lifecycle authority, deployment, or the integration worktree. "
            f"Trigger type: {trigger.trigger_type}. Evidence refs: {','.join(trigger.evidence_refs)}."
        )
        return (
            str(self._hermes),
            "--ignore-user-config",
            "--ignore-rules",
            "-t",
            "",
            "-z",
            prompt,
        )

    @staticmethod
    def _is_clean(worktree: Path) -> bool:
        checked = subprocess.run(
            ("git", "-C", str(worktree), "status", "--porcelain=v1", "--untracked-files=all"),
            check=False,
            capture_output=True,
            timeout=30,
        )
        return checked.returncode == 0 and not checked.stdout

    @staticmethod
    def _experiment_hashes(experiment: Path) -> tuple[str, ...]:
        return tuple(
            hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(experiment.rglob("*"))
            if path.is_file() and not path.is_symlink()
        )

    def _cleanup(self, task_root: Path, worktree: Path, worktree_added: bool) -> bool:
        if worktree_added:
            removed = subprocess.run(
                ("git", "-C", str(self._repository), "worktree", "remove", "--force", str(worktree)),
                check=False,
                capture_output=True,
                timeout=60,
            )
            if removed.returncode != 0:
                return False
        if task_root.exists():
            shutil.rmtree(task_root)
        return not task_root.exists()

    @staticmethod
    def _failed(
        reason: str,
        *,
        cleanup: bool,
        process_started: bool = False,
    ) -> ExecutionResult:
        return ExecutionResult(
            state="failed",
            result_summary=reason,
            result_sha256=None,
            evidence_sha256=(),
            cleanup_completed=cleanup,
            process_started=process_started,
            worktree_clean=False,
        )
__all__ = (
    "AutonomousTaskExecutor",
    "ExecutionResult",
    "ExecutionState",
    "IsolatedWorktreeExecutor",
)
