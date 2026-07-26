from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1
from trading_agent.dashboard_executable_binding import InvalidExecutableBindingError
from trading_agent.dashboard_execution_catalog import (
    ProductionExecutionId,
    _build_expected_execution,
)
from trading_agent.dashboard_execution_identity import (
    BoundExecutionIdentity,
    BoundExecutionRequest,
    BrokerOperation,
)
from trading_agent.dashboard_execution_sandbox import _ExecutionSandbox


class ProductionExecutionBoundary(Protocol):
    def blocker(self, trigger: AutonomousTriggerV1) -> str | None: ...

    def run_model(
        self,
        trigger: AutonomousTriggerV1,
        task_root: Path,
        experiment: Path,
        worktree: Path,
        prompt: str,
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]: ...

    def run_broker(
        self,
        trigger: AutonomousTriggerV1,
        task_root: Path,
        experiment: Path,
        worktree: Path,
        operation: BrokerOperation,
        parameters: tuple[str, ...],
        timeout: int,
    ) -> subprocess.CompletedProcess[bytes]: ...


def _create_production_boundary_factory():
    build_expected = _build_expected_execution
    sandbox_type = _ExecutionSandbox
    run_process = subprocess.run
    expected_repository = Path(__file__).resolve().parents[1]

    def create(
        *,
        repository: Path,
        source_evidence_root: Path,
        execution_id: ProductionExecutionId,
    ) -> ProductionExecutionBoundary:
        if type(execution_id) is not ProductionExecutionId:
            raise InvalidExecutableBindingError("production_execution_id_forbidden")
        bound_repository = repository.resolve(strict=True)
        if bound_repository != expected_repository:
            raise InvalidExecutableBindingError("production_repository_forbidden")
        bound_source = source_evidence_root.resolve(strict=False)

        def fresh() -> _ExecutionSandbox:
            identity = build_expected(bound_repository, execution_id)
            descriptor = build_expected(bound_repository, execution_id)

            def validate(candidate: BoundExecutionIdentity) -> None:
                if candidate != descriptor:
                    raise InvalidExecutableBindingError("execution_identity_not_rederived")
                candidate.revalidate()

            return sandbox_type(
                repository=bound_repository,
                source_evidence_root=bound_source,
                execution_identity=identity,
                fixture_mode=False,
                identity_validator=validate,
            )

        def run(
            boundary: _ExecutionSandbox,
            request: BoundExecutionRequest,
            trigger: AutonomousTriggerV1,
            task_root: Path,
            experiment: Path,
            worktree: Path,
            timeout: int,
        ) -> subprocess.CompletedProcess[bytes]:
            environment = boundary.environment(trigger, experiment)
            command = boundary.argv(request, task_root, worktree)
            boundary._revalidate_bindings()
            return run_process(
                command,
                cwd=worktree,
                env=environment,
                check=False,
                capture_output=True,
                timeout=timeout,
            )

        class _RederivedProductionBoundary:
            __slots__ = ()

            def blocker(self, trigger: AutonomousTriggerV1) -> str | None:
                return fresh().blocker(trigger)

            def run_model(
                self,
                trigger: AutonomousTriggerV1,
                task_root: Path,
                experiment: Path,
                worktree: Path,
                prompt: str,
                timeout: int,
            ) -> subprocess.CompletedProcess[bytes]:
                boundary = fresh()
                request = boundary.execution_identity.request(prompt)
                return run(boundary, request, trigger, task_root, experiment, worktree, timeout)

            def run_broker(
                self,
                trigger: AutonomousTriggerV1,
                task_root: Path,
                experiment: Path,
                worktree: Path,
                operation: BrokerOperation,
                parameters: tuple[str, ...],
                timeout: int,
            ) -> subprocess.CompletedProcess[bytes]:
                boundary = fresh()
                request = boundary.execution_identity.broker_request(operation, parameters)
                return run(boundary, request, trigger, task_root, experiment, worktree, timeout)

        return _RederivedProductionBoundary()

    return create


create_production_execution_boundary = _create_production_boundary_factory()

__all__ = (
    "ProductionExecutionBoundary",
    "create_production_execution_boundary",
)
