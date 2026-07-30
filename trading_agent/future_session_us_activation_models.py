from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from trading_agent.future_session_plan_models import FutureSessionUsRole

LaunchctlRunner = Callable[[tuple[str, ...]], int]


@dataclass(frozen=True, slots=True)
class FutureSessionActivationError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class ActivatedUsRoleArtifact:
    role: FutureSessionUsRole
    label: str
    source_plist: Path
    installed_plist: Path


@dataclass(frozen=True, slots=True)
class FutureSessionActivation:
    entries: tuple[ActivatedUsRoleArtifact, ...]
    receipt_path: Path


__all__ = (
    "ActivatedUsRoleArtifact",
    "FutureSessionActivation",
    "FutureSessionActivationError",
    "LaunchctlRunner",
)
