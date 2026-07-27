from __future__ import annotations

import re
import sys
from enum import StrEnum
from pathlib import Path
from typing import assert_never

from trading_agent.dashboard_executable_binding import (
    FileIdentity,
    InvalidExecutableBindingError,
    capture_file,
    capture_native_executable,
    capture_python_entrypoint,
)
from trading_agent.dashboard_execution_identity import (
    BoundExecutionIdentity,
    _build_native_identity,
    _build_python_identity,
)


class ProductionExecutionId(StrEnum):
    HERMES_MODEL = "hermes-model"
    HERMES_PROBE = "hermes-probe"
    HEALTH_BROKER = "health-broker"
    RESEARCH_BROKER = "research-broker"


def _create_descriptor_builder():
    capture_file_ref = capture_file
    capture_native_ref = capture_native_executable
    capture_entrypoint_ref = capture_python_entrypoint
    build_native_ref = _build_native_identity
    build_python_ref = _build_python_identity
    wrapper_pattern = re.compile(r'^exec "([^"]+)" "\$@"$')
    interpreter_path = Path(sys.executable).resolve(strict=True)

    def wrapper_target(wrapper: FileIdentity) -> Path:
        matches = tuple(
            match
            for line in wrapper.path.read_text().splitlines()
            if (match := wrapper_pattern.fullmatch(line.strip()))
        )
        if len(matches) != 1:
            raise InvalidExecutableBindingError("hermes_wrapper_invalid")
        return Path(matches[0].group(1))

    def hermes(
        repository: Path,
        role: ProductionExecutionId,
    ) -> BoundExecutionIdentity:
        agent_root = Path.home() / ".hermes" / "hermes-agent"
        wrapper = capture_file_ref(Path.home() / ".local" / "bin" / "hermes", executable=True)
        direct_path = wrapper_target(wrapper)
        expected_direct = (agent_root / "venv" / "bin" / "hermes").resolve(strict=True)
        if direct_path.resolve(strict=True) != expected_direct:
            raise InvalidExecutableBindingError("hermes_wrapper_target_forbidden")
        entrypoint, interpreter = capture_entrypoint_ref(direct_path)
        execution_role = "hermes-model" if role is ProductionExecutionId.HERMES_MODEL else "hermes-probe"
        return build_python_ref(
            execution_role,
            repository,
            interpreter,
            entrypoint,
            agent_root / "hermes_cli",
            (interpreter.path.parents[1], agent_root),
            readable_literals=(
                agent_root / ".env",
                Path.home() / ".hermes" / "auth.json",
            ),
            test_only=False,
            supporting_files=(wrapper,),
        )

    def build(
        repository: Path,
        execution_id: ProductionExecutionId,
    ) -> BoundExecutionIdentity:
        if type(execution_id) is not ProductionExecutionId:
            raise InvalidExecutableBindingError("production_execution_id_forbidden")
        match execution_id:
            case ProductionExecutionId.HEALTH_BROKER:
                return build_native_ref(capture_native_ref(Path("/usr/bin/true")))
            case ProductionExecutionId.RESEARCH_BROKER:
                interpreter = capture_native_ref(interpreter_path)
                target = capture_file_ref(
                    repository / "trading_agent" / "dashboard_research_broker.py",
                    executable=True,
                )
                return build_python_ref(
                    "research-broker",
                    repository,
                    interpreter,
                    target,
                    None,
                    (interpreter.path.parents[1], repository),
                    readable_literals=(),
                    test_only=False,
                )
            case ProductionExecutionId.HERMES_MODEL | ProductionExecutionId.HERMES_PROBE:
                return hermes(repository, execution_id)
            case unexpected:
                assert_never(unexpected)

    return build


_build_expected_execution = _create_descriptor_builder()

__all__ = ("ProductionExecutionId",)
