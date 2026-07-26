from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1


@dataclass(frozen=True, slots=True)
class AutonomousExecutionSandbox:
    repository: Path
    source_evidence_root: Path
    hermes_executable: Path
    fixture_mode: bool

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
        if (
            self.hermes_executable.is_symlink()
            or not self.hermes_executable.is_file()
            or not os.access(self.hermes_executable, os.X_OK)
        ):
            return "pinned_hermes_invalid"
        if not Path("/usr/bin/sandbox-exec").is_file():
            return "sandbox_runtime_missing"
        return None

    def argv(
        self,
        command: tuple[str, ...],
        task_root: Path,
        worktree: Path,
    ) -> tuple[str, ...]:
        readable = (
            "/System",
            "/Library",
            "/usr/lib",
            "/usr/share",
            "/bin",
            "/usr/bin",
            str(worktree.resolve(strict=True)),
            str(self.source_evidence_root.resolve(strict=True)),
            str(self.hermes_executable),
        )
        profile = "\n".join(
            (
                "(version 1)",
                "(deny default)",
                '(import "system.sb")',
                "(allow process*)",
                "(allow sysctl-read)",
                "(allow file-read-metadata)",
                *(f"(allow file-read* (subpath {json.dumps(path)}))" for path in readable),
                f"(allow file-write* (subpath {json.dumps(str(task_root.resolve(strict=True)))}))",
            )
        )
        return ("/usr/bin/sandbox-exec", "-p", profile, *command)

    def environment(self, trigger: AutonomousTriggerV1, experiment: Path) -> dict[str, str]:
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


def _write_path_allowed(value: str) -> bool:
    requested = Path(value)
    return (
        not requested.is_absolute()
        and ".." not in requested.parts
        and len(requested.parts) >= 2
        and requested.parts[0] == "experiment"
    )


__all__ = ("AutonomousExecutionSandbox",)
