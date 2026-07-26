from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

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

ProductionExecutionId = Literal[
    "hermes-model",
    "hermes-probe",
    "health-broker",
    "research-broker",
]
_PRODUCTION_IDS: tuple[ProductionExecutionId, ...] = (
    "hermes-model",
    "hermes-probe",
    "health-broker",
    "research-broker",
)
_WRAPPER_EXEC = re.compile(r'^exec "([^"]+)" "\$@"$')


@dataclass(frozen=True, slots=True)
class _SealedProductionSelection:
    execution_id: ProductionExecutionId
    identity: BoundExecutionIdentity
    descriptor: BoundExecutionIdentity

    def validate(self, candidate: BoundExecutionIdentity) -> None:
        if candidate is not self.identity or candidate != self.descriptor:
            raise InvalidExecutableBindingError("execution_identity_not_sealed")
        candidate.revalidate()


@dataclass(frozen=True, slots=True)
class _SealedProductionCatalog:
    entries: tuple[_SealedProductionSelection, ...]

    def select(self, execution_id: ProductionExecutionId) -> _SealedProductionSelection:
        for entry in self.entries:
            if entry.execution_id == execution_id:
                return entry
        raise InvalidExecutableBindingError("production_execution_id_forbidden")


def _create_selector():
    repository = Path(__file__).resolve().parents[1]
    catalog = _SealedProductionCatalog(
        tuple(
            _SealedProductionSelection(
                fixed_id,
                _build_descriptor(repository, fixed_id),
                _build_descriptor(repository, fixed_id),
            )
            for fixed_id in _PRODUCTION_IDS
        )
    )

    def select(
        requested_repository: Path,
        execution_id: ProductionExecutionId,
    ) -> _SealedProductionSelection:
        if requested_repository.resolve(strict=True) != repository:
            raise InvalidExecutableBindingError("production_repository_forbidden")
        return catalog.select(execution_id)

    return select


def _build_descriptor(
    repository: Path,
    execution_id: ProductionExecutionId,
) -> BoundExecutionIdentity:
    match execution_id:
        case "health-broker":
            return _build_native_identity(capture_native_executable(Path("/usr/bin/true")))
        case "research-broker":
            interpreter = capture_native_executable(Path(sys.executable).resolve(strict=True))
            target = capture_file(
                repository / "trading_agent" / "dashboard_research_broker.py",
                executable=True,
            )
            return _build_python_identity(
                "research-broker",
                repository,
                interpreter,
                target,
                None,
                (interpreter.path.parents[1], repository),
                readable_literals=(),
                test_only=False,
            )
        case "hermes-model" | "hermes-probe":
            return _build_hermes_descriptor(repository, execution_id)
        case unexpected:
            assert_never(unexpected)


def _build_hermes_descriptor(
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
    return _build_python_identity(
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


_select_production_execution = _create_selector()

__all__ = ("ProductionExecutionId",)
