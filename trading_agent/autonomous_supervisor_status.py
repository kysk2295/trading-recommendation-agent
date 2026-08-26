from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from trading_agent.autonomous_task_models import AutonomousTaskId, AutonomousTaskState
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.research_agent_service_config import ResearchAgentServiceConfig

_NONTERMINAL: Final = frozenset(AutonomousTaskState) - frozenset(
    {AutonomousTaskState.COMPLETED, AutonomousTaskState.ABANDONED}
)


@dataclass(frozen=True, slots=True)
class AutonomousSupervisorPaths:
    task_database: Path
    memory_database: Path


class AutonomousSupervisorStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    enabled: Literal[True] = True
    total_tasks: int = Field(ge=0)
    nonterminal_tasks: int = Field(ge=0)
    blocked_tasks: int = Field(ge=0)
    next_wake_at: AwareDatetime | None
    last_task_id: AutonomousTaskId | None


def autonomous_supervisor_paths(config: ResearchAgentServiceConfig) -> AutonomousSupervisorPaths:
    root = config.output_root / "autonomous-supervisor"
    return AutonomousSupervisorPaths(root / "tasks.sqlite3", root / "memory.sqlite3")


def autonomous_supervisor_status(
    tasks: AutonomousTaskStore,
    now: dt.datetime,
) -> AutonomousSupervisorStatus:
    _ = now.astimezone(dt.UTC)
    durable = tasks.reader().tasks()
    open_tasks = tuple(task for task in durable if task.state in _NONTERMINAL)
    wakes = tuple(task.next_wake_at for task in open_tasks if task.next_wake_at is not None)
    latest = max(durable, key=lambda task: (task.updated_at, task.task_id), default=None)
    return AutonomousSupervisorStatus(
        total_tasks=len(durable),
        nonterminal_tasks=len(open_tasks),
        blocked_tasks=sum(task.state is AutonomousTaskState.BLOCKED for task in open_tasks),
        next_wake_at=min(wakes, default=None),
        last_task_id=None if latest is None else latest.task_id,
    )


def autonomous_supervisor_status_for_config(
    config: ResearchAgentServiceConfig,
    now: dt.datetime,
) -> AutonomousSupervisorStatus:
    tasks = AutonomousTaskStore(autonomous_supervisor_paths(config).task_database)
    try:
        return autonomous_supervisor_status(tasks, now)
    finally:
        tasks.close()


__all__ = (
    "AutonomousSupervisorPaths",
    "AutonomousSupervisorStatus",
    "autonomous_supervisor_paths",
    "autonomous_supervisor_status",
    "autonomous_supervisor_status_for_config",
)
