from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import GeneratedStrategyArtifactStore
from trading_agent.us_forward_shadow_artifacts import UsForwardShadowArtifactStore


class InvalidUsForwardShadowRuntimeError(RuntimeError):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"us_forward_shadow_runtime_blocked:{self.reason}"


@dataclass(frozen=True, slots=True)
class UsForwardShadowServices:
    ledger: ExperimentLedgerStore
    generated_artifacts: GeneratedStrategyArtifactStore
    shadow_artifacts: UsForwardShadowArtifactStore
    task_root: Path


__all__ = ("InvalidUsForwardShadowRuntimeError", "UsForwardShadowServices")
