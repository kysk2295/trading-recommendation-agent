from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1
from trading_agent.dashboard_executable_binding import (
    ExecutableIdentity,
    InvalidExecutableBindingError,
    capture_executable,
    capture_interpreter,
)


@dataclass(frozen=True, slots=True)
class AutonomousExecutionSandbox:
    repository: Path
    source_evidence_root: Path
    hermes_executable: Path
    fixture_mode: bool
    allowed_tool_executables: tuple[Path, ...] = ()
    _bindings: tuple[ExecutableIdentity, ...] = ()
    _runtime_bindings: tuple[ExecutableIdentity, ...] = ()
    _binding_error: str | None = None

    def __post_init__(self) -> None:
        try:
            bindings = tuple(
                capture_executable(path)
                for path in (self.hermes_executable, *self.allowed_tool_executables)
            )
            interpreters = tuple(
                capture_interpreter(binding.interpreter)
                for binding in bindings
                if binding.interpreter is not None
            )
        except InvalidExecutableBindingError as error:
            object.__setattr__(self, "_binding_error", error.reason)
        else:
            object.__setattr__(self, "_bindings", bindings)
            object.__setattr__(self, "_runtime_bindings", bindings + interpreters)

    def blocker(self, trigger: AutonomousTriggerV1) -> str | None:
        if trigger.environment_spec.allowed_read_roots != ("isolated_worktree", "source_evidence"):
            return "read_root_policy_forbidden"
        if trigger.environment_spec.allowed_write_roots != ("experiment",):
            return "write_root_policy_forbidden"
        if any(tool not in {"read_evidence", "write_candidate"} for tool in trigger.environment_spec.allowed_tools):
            return "tool_policy_forbidden"
        if trigger.environment_spec.requested_tool_argv:
            return "tool_argv_forbidden"
        if any(not self._read_path_allowed(path) for path in trigger.environment_spec.requested_read_paths):
            return "read_path_forbidden"
        if any(not _write_path_allowed(path) for path in trigger.environment_spec.requested_write_paths):
            return "write_path_forbidden"
        if trigger.environment_spec.requested_network_targets:
            return "network_target_forbidden"
        if trigger.environment_spec.network_policy == "public_read_only":
            return "network_policy_forbidden"
        if trigger.environment_spec.network_policy == "model_provider_only" and not self.fixture_mode:
            return "provider_proxy_required"
        if not self.source_evidence_root.is_dir() or self.source_evidence_root.is_symlink():
            return "source_evidence_root_invalid"
        if self._binding_error is not None:
            return self._binding_error
        try:
            self._revalidate_bindings()
        except InvalidExecutableBindingError as error:
            return error.reason
        if not Path("/usr/bin/sandbox-exec").is_file():
            return "sandbox_runtime_missing"
        return None

    def argv(
        self,
        command: tuple[str, ...],
        task_root: Path,
        worktree: Path,
    ) -> tuple[str, ...]:
        self._revalidate_bindings()
        command_binding = self._binding_for_command(command)
        executable_paths = {command_binding.path}
        if command_binding.interpreter is not None:
            executable_paths.add(command_binding.interpreter)
        readable = (
            "/System",
            "/Library",
            "/usr/lib",
            "/usr/share",
            str(worktree.resolve(strict=True)),
            str(self.source_evidence_root.resolve(strict=True)),
        )
        profile = "\n".join(
            (
                "(version 1)",
                "(deny default)",
                '(import "system.sb")',
                "(deny process-fork)",
                "(deny process-exec)",
                "(allow sysctl-read)",
                *(f"(allow file-read* (subpath {json.dumps(path)}))" for path in readable),
                *(
                    f"(allow file-read* (literal {json.dumps(str(path))}))"
                    for path in sorted(executable_paths)
                ),
                *(
                    f"(allow process-exec (literal {json.dumps(str(path))}))"
                    for path in sorted(executable_paths)
                ),
                f"(allow file-write* (subpath {json.dumps(str(task_root.resolve(strict=True)))}))",
            )
        )
        return ("/usr/bin/sandbox-exec", "-p", profile, str(command_binding.path), *command[1:])

    def environment(self, trigger: AutonomousTriggerV1, experiment: Path) -> dict[str, str]:
        self._revalidate_bindings()
        task_root = experiment.parent
        home = task_root / "home"
        temporary = task_root / "tmp"
        binary = task_root / "bin"
        for path in (home, temporary, binary):
            path.mkdir(mode=0o700)
        return {
            "DASHBOARD_AGENT_FAMILY": trigger.agent_family_id,
            "DASHBOARD_AUTONOMOUS_CHANNEL": "1",
            "DASHBOARD_EXPERIMENT_ROOT": str(experiment.resolve(strict=True)),
            "DASHBOARD_MEMORY_NAMESPACE": f"research-family:{trigger.agent_family_id}:memory-v1",
            "DASHBOARD_NETWORK_POLICY": "none" if self.fixture_mode else trigger.environment_spec.network_policy,
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

    def _binding_for_command(self, command: tuple[str, ...]) -> ExecutableIdentity:
        if not command:
            raise InvalidExecutableBindingError("executable_command_empty")
        requested = Path(command[0])
        if requested.is_symlink():
            raise InvalidExecutableBindingError("executable_symlink_forbidden")
        if ".." in requested.parts:
            raise InvalidExecutableBindingError("executable_path_traversal")
        try:
            normalized = requested.resolve(strict=True)
        except OSError as error:
            raise InvalidExecutableBindingError("executable_unavailable") from error
        for binding in self._bindings:
            if normalized == binding.path:
                return binding
        raise InvalidExecutableBindingError("executable_not_registered")

    def _revalidate_bindings(self) -> None:
        if self._binding_error is not None:
            raise InvalidExecutableBindingError(self._binding_error)
        for expected in self._runtime_bindings:
            actual = capture_executable(expected.path)
            if actual != expected:
                raise InvalidExecutableBindingError("executable_identity_changed")


def _write_path_allowed(value: str) -> bool:
    requested = Path(value)
    return (
        not requested.is_absolute()
        and ".." not in requested.parts
        and len(requested.parts) >= 2
        and requested.parts[0] == "experiment"
    )


__all__ = (
    "AutonomousExecutionSandbox",
    "ExecutableIdentity",
    "InvalidExecutableBindingError",
)
