from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from trading_agent.dashboard_autonomous_research import AutonomousTriggerV1

ExecutionState = Literal["completed", "failed", "uncertain"]


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    state: ExecutionState
    result_summary: str
    result_sha256: str | None
    evidence_sha256: tuple[str, ...]
    cleanup_completed: bool
    process_started: bool
    worktree_clean: bool


class AutonomousTaskExecutor(Protocol):
    def preflight(self, trigger: AutonomousTriggerV1) -> str | None: ...

    def execute(self, trigger: AutonomousTriggerV1, task_id: str) -> ExecutionResult: ...


__all__ = (
    "AutonomousTaskExecutor",
    "ExecutionResult",
    "ExecutionState",
)
