from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1
from trading_agent.dashboard_executable_binding import (
    InvalidExecutableBindingError,
)
from trading_agent.dashboard_execution_catalog import (
    ProductionExecutionId,
    _select_production_execution,
)
from trading_agent.dashboard_execution_identity import (
    BoundExecutionIdentity,
    BoundExecutionRequest,
)

IdentityValidator = Callable[[BoundExecutionIdentity], None]


@dataclass(frozen=True, slots=True)
class _ExecutionSandbox:
    repository: Path
    source_evidence_root: Path
    execution_identity: BoundExecutionIdentity
    fixture_mode: bool
    identity_validator: IdentityValidator
    _binding_error: str | None = None

    def __post_init__(self) -> None:
        try:
            self.identity_validator(self.execution_identity)
        except InvalidExecutableBindingError as error:
            object.__setattr__(self, "_binding_error", error.reason)

    def blocker(self, trigger: AutonomousTriggerV1) -> str | None:
        if trigger.environment_spec.allowed_read_roots != ("isolated_worktree", "source_evidence"):
            return "read_root_policy_forbidden"
        if trigger.environment_spec.allowed_write_roots != ("experiment",):
            return "write_root_policy_forbidden"
        if any(
            tool not in {"read_evidence", "write_candidate", "run_tests"}
            for tool in trigger.environment_spec.allowed_tools
        ):
            return "tool_policy_forbidden"
        if trigger.environment_spec.requested_tool_argv:
            return "tool_argv_forbidden"
        if any(not self._read_path_allowed(path) for path in trigger.environment_spec.requested_read_paths):
            return "read_path_forbidden"
        if any(not _write_path_allowed(path) for path in trigger.environment_spec.requested_write_paths):
            return "write_path_forbidden"
        if trigger.environment_spec.requested_network_targets:
            return "network_target_forbidden"
        if self._binding_error is not None:
            return self._binding_error
        if trigger.environment_spec.network_policy == "public_read_only":
            return "network_policy_forbidden"
        if (
            trigger.environment_spec.network_policy == "model_provider_only"
            and not self.fixture_mode
            and self.execution_identity.role in {"hermes-model", "hermes-probe"}
        ):
            return "provider_proxy_required"
        if not self.source_evidence_root.is_dir() or self.source_evidence_root.is_symlink():
            return "source_evidence_root_invalid"
        try:
            self._revalidate_bindings()
        except InvalidExecutableBindingError as error:
            return error.reason
        if not Path("/usr/bin/sandbox-exec").is_file():
            return "sandbox_runtime_missing"
        return None

    def argv(
        self,
        request: BoundExecutionRequest,
        task_root: Path,
        worktree: Path,
    ) -> tuple[str, ...]:
        self._revalidate_bindings()
        if not self.execution_identity.accepts(request):
            raise InvalidExecutableBindingError("execution_request_not_bound")
        readable_roots = (
            "/System",
            "/Library",
            "/usr/lib",
            "/usr/share",
            str(worktree.resolve(strict=True)),
            str(self.source_evidence_root.resolve(strict=True)),
            *(str(path.resolve(strict=True)) for path in self.execution_identity.readable_roots),
        )
        readable_files = tuple(
            str(identity.path)
            for identity in (self.execution_identity.launcher, self.execution_identity.target)
            if identity is not None
        )
        metadata_files = tuple(str(path) for path in self.execution_identity.readable_literals)
        executable = str(self.execution_identity.executable.path)
        profile = "\n".join(
            (
                "(version 1)",
                "(deny default)",
                '(import "system.sb")',
                "(deny process-fork)",
                "(deny process-exec)",
                "(allow sysctl-read)",
                *(f"(allow file-read* (subpath {json.dumps(path)}))" for path in readable_roots),
                *(f"(allow file-read-metadata (subpath {json.dumps(path)}))" for path in readable_roots),
                *(f"(allow file-read* (literal {json.dumps(path)}))" for path in readable_files),
                *(f"(allow file-read-metadata (literal {json.dumps(path)}))" for path in metadata_files),
                f"(allow file-read* (subpath {json.dumps(str(task_root.resolve(strict=True)))}))",
                f"(allow file-read* (literal {json.dumps(executable)}))",
                f"(allow process-exec (literal {json.dumps(executable)}))",
                f"(allow file-write* (subpath {json.dumps(str(task_root.resolve(strict=True)))}))",
            )
        )
        return ("/usr/bin/sandbox-exec", "-p", profile, *request.argv)

    def environment(self, trigger: AutonomousTriggerV1, experiment: Path) -> dict[str, str]:
        self._revalidate_bindings()
        task_root = experiment.parent
        home = task_root / "home"
        temporary = task_root / "tmp"
        binary = task_root / "bin"
        for path in (home, temporary, binary):
            path.mkdir(mode=0o700, exist_ok=True)
        return {
            "DASHBOARD_AGENT_FAMILY": trigger.agent_family_id,
            "DASHBOARD_AUTONOMOUS_CHANNEL": "1",
            "DASHBOARD_EXPERIMENT_ROOT": str(experiment.resolve(strict=True)),
            "DASHBOARD_MEMORY_NAMESPACE": f"research-family:{trigger.agent_family_id}:memory-v1",
            "DASHBOARD_NETWORK_POLICY": (
                trigger.environment_spec.network_policy
                if self.execution_identity.role in {"hermes-model", "hermes-probe"}
                else "none"
            ),
            "DASHBOARD_PINNED_TARGET": (
                "" if self.execution_identity.target is None else str(self.execution_identity.target.path)
            ),
            "DASHBOARD_SOURCE_EVIDENCE_ROOT": str(self.source_evidence_root.resolve(strict=True)),
            "HOME": str(home.resolve(strict=True)),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": str(binary.resolve(strict=True)),
            "TMPDIR": str(temporary.resolve(strict=True)),
        }

    def _read_path_allowed(self, value: str) -> bool:
        requested = Path(value)
        if requested.is_absolute() or ".." in requested.parts or not requested.parts:
            return False
        try:
            if requested.parts[0] == "source_evidence":
                root = self.source_evidence_root.resolve(strict=True)
            elif requested.parts[0] == "isolated_worktree":
                root = self.repository.resolve(strict=True)
            else:
                return False
            candidate = root.joinpath(*requested.parts[1:]).resolve(strict=True)
        except OSError:
            return False
        return candidate == root or root in candidate.parents

    def _revalidate_bindings(self) -> None:
        if self._binding_error is not None:
            raise InvalidExecutableBindingError(self._binding_error)
        self.identity_validator(self.execution_identity)


def _create_production_sandbox_factory():
    selector = _select_production_execution

    def create(
        *,
        repository: Path,
        source_evidence_root: Path,
        execution_id: ProductionExecutionId,
    ) -> _ExecutionSandbox:
        selection = selector(repository, execution_id)
        return _ExecutionSandbox(
            repository=repository.resolve(strict=True),
            source_evidence_root=source_evidence_root.resolve(strict=False),
            execution_identity=selection.identity,
            fixture_mode=False,
            identity_validator=selection.validate,
        )

    return create


create_production_execution_sandbox = _create_production_sandbox_factory()


def _write_path_allowed(value: str) -> bool:
    requested = Path(value)
    return (
        not requested.is_absolute()
        and ".." not in requested.parts
        and len(requested.parts) >= 2
        and requested.parts[0] == "experiment"
    )


__all__ = (
    "InvalidExecutableBindingError",
    "create_production_execution_sandbox",
)
