from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Literal

from trading_agent.dashboard_autonomous_publisher import _handle_autonomous_trigger
from trading_agent.dashboard_autonomous_research import (
    AutonomousTaskReceiptV1,
    AutonomousTriggerV1,
)
from trading_agent.dashboard_executable_binding import (
    InvalidExecutableBindingError,
    capture_file,
    capture_native_executable,
)
from trading_agent.dashboard_execution_identity import (
    BoundExecutionIdentity,
    _build_python_identity,
)
from trading_agent.dashboard_execution_sandbox import (
    _ExecutionSandbox,
    create_production_execution_sandbox,
)
from trading_agent.dashboard_worktree_executor import _IsolatedWorktreeExecutorCore

FixtureScenario = Literal["model", "exec-escape", "filesystem-escape", "network-escape"]
_FIXTURE_TARGETS: dict[FixtureScenario, str] = {
    "model": "fake_hermes.py",
    "exec-escape": "fake_hermes_exec_escape.py",
    "filesystem-escape": "fake_hermes_filesystem_escape.py",
    "network-escape": "fake_hermes_network_escape.py",
}


def fixture_identity(
    repository: Path,
    scenario: FixtureScenario = "model",
) -> BoundExecutionIdentity:
    target = capture_file(
        repository / "tests" / "fixtures" / "dashboard" / _FIXTURE_TARGETS[scenario],
        executable=True,
    )
    interpreter = capture_native_executable(Path(sys.executable).resolve(strict=True))
    return _build_python_identity(
        "fixture-model",
        repository,
        interpreter,
        target,
        None,
        (interpreter.path.parents[1], repository),
        readable_literals=(),
        test_only=True,
    )


def execution_sandbox(
    repository: Path,
    source_evidence_root: Path,
    identity: BoundExecutionIdentity,
) -> _ExecutionSandbox:
    descriptor = copy.deepcopy(identity)

    def validate(candidate: BoundExecutionIdentity) -> None:
        if candidate is not identity or candidate != descriptor:
            raise InvalidExecutableBindingError("test_execution_identity_not_sealed")
        candidate.revalidate()

    return _ExecutionSandbox(
        repository=repository,
        source_evidence_root=source_evidence_root,
        execution_identity=identity,
        fixture_mode=True,
        identity_validator=validate,
    )


def worktree_executor(
    *,
    repository: Path,
    environment_root: Path,
    source_evidence_root: Path,
) -> _IsolatedWorktreeExecutorCore:
    identity = fixture_identity(repository)
    return _IsolatedWorktreeExecutorCore(
        repository=repository,
        environment_root=environment_root,
        sandbox=execution_sandbox(repository, source_evidence_root, identity),
        broker_sandbox=create_production_execution_sandbox(
            repository=repository,
            source_evidence_root=source_evidence_root,
            execution_id="research-broker",
        ),
    )


def run_autonomous_trigger(
    trigger: AutonomousTriggerV1,
    *,
    state_root: Path,
    receipts: list[AutonomousTaskReceiptV1],
):
    repository = Path(__file__).resolve().parents[1]
    return _handle_autonomous_trigger(
        trigger,
        state_root,
        receipts,
        worktree_executor(
            repository=repository,
            environment_root=state_root / "environments",
            source_evidence_root=state_root / "authorities",
        ),
    )


__all__ = (
    "FixtureScenario",
    "execution_sandbox",
    "fixture_identity",
    "run_autonomous_trigger",
    "worktree_executor",
)
