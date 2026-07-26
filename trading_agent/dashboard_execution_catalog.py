from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Literal

from trading_agent.dashboard_executable_binding import (
    FileIdentity,
    InvalidExecutableBindingError,
    capture_file,
    capture_native_executable,
    capture_python_entrypoint,
)
from trading_agent.dashboard_execution_identity import (
    BoundExecutionIdentity,
    bind_native_identity,
    bind_python_identity,
)

FixtureScenario = Literal["model", "exec-escape", "filesystem-escape", "network-escape"]
_WRAPPER_EXEC = re.compile(r'^exec "([^"]+)" "\$@"$')
_FIXTURE_TARGETS: dict[FixtureScenario, str] = {
    "model": "fake_hermes.py",
    "exec-escape": "fake_hermes_exec_escape.py",
    "filesystem-escape": "fake_hermes_filesystem_escape.py",
    "network-escape": "fake_hermes_network_escape.py",
}


def production_hermes_identity(repository: Path) -> BoundExecutionIdentity:
    return _production_hermes_identity(repository, "hermes-model")


def production_hermes_probe_identity(repository: Path) -> BoundExecutionIdentity:
    return _production_hermes_identity(repository, "hermes-probe")


def fixture_hermes_identity_for_tests(repository: Path) -> BoundExecutionIdentity:
    return fixture_scenario_identity_for_tests(repository, "model")


def fixture_scenario_identity_for_tests(
    repository: Path,
    scenario: FixtureScenario,
) -> BoundExecutionIdentity:
    target = capture_file(
        repository / "tests" / "fixtures" / "dashboard" / _FIXTURE_TARGETS[scenario],
        executable=True,
    )
    interpreter = capture_native_executable(Path(sys.executable).resolve(strict=True))
    return bind_python_identity(
        "fixture-model",
        repository,
        interpreter,
        target,
        None,
        (interpreter.path.parents[1], repository),
        readable_literals=(),
        test_only=True,
    )


def health_broker_identity() -> BoundExecutionIdentity:
    return bind_native_identity(capture_native_executable(Path("/usr/bin/true")))


def _production_hermes_identity(
    repository: Path,
    role: Literal["hermes-model", "hermes-probe"],
) -> BoundExecutionIdentity:
    agent_root = Path.home() / ".hermes" / "hermes-agent"
    wrapper = capture_file(Path.home() / ".local" / "bin" / "hermes", executable=True)
    direct_path = _wrapper_target(wrapper)
    expected_direct = (agent_root / "venv" / "bin" / "hermes").resolve(strict=True)
    if direct_path.resolve(strict=True) != expected_direct:
        raise InvalidExecutableBindingError("hermes_wrapper_target_forbidden")
    entrypoint, interpreter = capture_python_entrypoint(direct_path)
    return bind_python_identity(
        role,
        repository,
        interpreter,
        entrypoint,
        agent_root / "hermes_cli",
        (interpreter.path.parents[1], agent_root),
        readable_literals=(agent_root / ".env",),
        test_only=False,
        supporting_files=(wrapper,),
    )


def _wrapper_target(wrapper: FileIdentity) -> Path:
    lines = wrapper.path.read_text().splitlines()
    matches = tuple(match for line in lines if (match := _WRAPPER_EXEC.fullmatch(line.strip())))
    if len(matches) != 1:
        raise InvalidExecutableBindingError("hermes_wrapper_invalid")
    return Path(matches[0].group(1))


__all__ = (
    "FixtureScenario",
    "fixture_hermes_identity_for_tests",
    "fixture_scenario_identity_for_tests",
    "health_broker_identity",
    "production_hermes_identity",
    "production_hermes_probe_identity",
)
