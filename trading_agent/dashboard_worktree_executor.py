from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1
from trading_agent.dashboard_execution_catalog import ProductionExecutionId
from trading_agent.dashboard_isolated_worktree_support import (
    autonomous_prompt,
    cleanup_isolated_worktree,
    experiment_hashes,
    isolated_worktree_clean,
)
from trading_agent.dashboard_outbound_redaction import redact_outbound_text
from trading_agent.dashboard_production_execution_boundary import (
    ProductionExecutionBoundary,
    create_production_execution_boundary,
)
from trading_agent.dashboard_research_broker_contract import InvalidResearchBrokerCommandError
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)

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


class _IsolatedWorktreeExecutorCore:
    def __init__(
        self,
        *,
        repository: Path,
        environment_root: Path,
        source_evidence_root: Path,
        sandbox: ProductionExecutionBoundary,
        broker_sandbox: ProductionExecutionBoundary,
    ) -> None:
        self._repository = repository.resolve()
        self._environment_root = environment_root.resolve()
        self._source_evidence_root = source_evidence_root.resolve()
        self._sandbox = sandbox
        self._broker_sandbox = broker_sandbox

    def preflight(self, trigger: AutonomousTriggerV1) -> str | None:
        if not {"read_evidence", "write_candidate", "run_tests"}.issubset(
            trigger.environment_spec.allowed_tools
        ):
            return "required_research_tools_missing"
        return self._sandbox.blocker(trigger) or self._broker_sandbox.blocker(trigger)

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
                query = self._run_broker(
                    trigger,
                    task_root,
                    worktree,
                    "evidence-query",
                    trigger.evidence_refs,
                )
                if query.returncode != 0:
                    raise InvalidResearchBrokerCommandError("evidence_query_failed")
                completed = self._sandbox.run_model(
                    trigger,
                    task_root,
                    experiment,
                    worktree,
                    autonomous_prompt(
                        trigger,
                        self._source_evidence(trigger),
                    ),
                    trigger.budget_envelope.max_runtime_seconds,
                )
                process_started = True
                clean = isolated_worktree_clean(worktree)
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
                    registered = self._run_broker(
                        trigger,
                        task_root,
                        worktree,
                        "hypothesis-register",
                        (trigger.trigger_id, trigger.agent_family_id, trigger.payload_sha256),
                    )
                    tested = self._run_broker(
                        trigger,
                        task_root,
                        worktree,
                        "experiment-run",
                        (trigger.trigger_id,),
                    )
                    if registered.returncode != 0 or tested.returncode != 0:
                        result = self._failed(
                            "research_broker_failed",
                            cleanup=False,
                            process_started=True,
                        )
                    else:
                        result = ExecutionResult(
                            state="completed",
                            result_summary=redact_outbound_text(
                                stdout.decode("utf-8", errors="replace").strip()
                            ),
                            result_sha256=result_hash,
                            evidence_sha256=experiment_hashes(experiment),
                            cleanup_completed=False,
                            process_started=True,
                            worktree_clean=True,
                        )
        except subprocess.TimeoutExpired:
            process_started = True
            result = self._failed("autonomous_process_timeout", cleanup=False, process_started=True)
        except InvalidResearchBrokerCommandError as error:
            result = self._failed(error.reason, cleanup=False, process_started=process_started)
        except OSError:
            result = self._failed(
                "autonomous_process_start_failed",
                cleanup=False,
                process_started=process_started,
            )
        finally:
            cleanup = cleanup_isolated_worktree(
                self._repository,
                task_root,
                worktree,
                worktree_added,
            )
        return ExecutionResult(
            state=result.state,
            result_summary=result.result_summary,
            result_sha256=result.result_sha256,
            evidence_sha256=result.evidence_sha256,
            cleanup_completed=cleanup,
            process_started=result.process_started,
            worktree_clean=result.worktree_clean,
        )

    def _run_broker(
        self,
        trigger: AutonomousTriggerV1,
        task_root: Path,
        worktree: Path,
        operation: Literal["evidence-query", "hypothesis-register", "experiment-run"],
        parameters: tuple[str, ...],
    ) -> subprocess.CompletedProcess[bytes]:
        return self._broker_sandbox.run_broker(
            trigger,
            task_root,
            task_root / "experiment",
            worktree,
            operation,
            parameters,
            min(trigger.budget_envelope.max_runtime_seconds, 30),
        )

    def _source_evidence(self, trigger: AutonomousTriggerV1) -> str | None:
        path = (
            self._source_evidence_root
            / "evidence"
            / f"{trigger.trigger_id}.json"
        )
        try:
            payload = read_private_text_query_only(path)
        except InvalidPrivateQueryFileError:
            return None
        if (
            len(payload.encode()) > 32 * 1024
            or hashlib.sha256(payload.encode()).hexdigest()
            != trigger.payload_sha256
        ):
            return None
        return payload

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


class IsolatedWorktreeExecutor(_IsolatedWorktreeExecutorCore):
    def __init__(
        self,
        *,
        repository: Path,
        environment_root: Path,
        source_evidence_root: Path,
        execution_id: ProductionExecutionId = ProductionExecutionId.HERMES_MODEL,
    ) -> None:
        super().__init__(
            repository=repository,
            environment_root=environment_root,
            source_evidence_root=source_evidence_root,
            sandbox=create_production_execution_boundary(
                repository=repository,
                source_evidence_root=source_evidence_root,
                execution_id=execution_id,
            ),
            broker_sandbox=create_production_execution_boundary(
                repository=repository,
                source_evidence_root=source_evidence_root,
                execution_id=ProductionExecutionId.RESEARCH_BROKER,
            ),
        )


__all__ = (
    "AutonomousTaskExecutor",
    "ExecutionResult",
    "ExecutionState",
    "IsolatedWorktreeExecutor",
)
