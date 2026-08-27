from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from trading_agent.autonomous_task_models import AutonomousTaskId, AutonomousTaskState
from trading_agent.autonomous_task_store import AutonomousTaskStore
from trading_agent.kr_autonomous_trade_models import KrAutonomousTradeOutcome
from trading_agent.kr_autonomous_trade_store import KrAutonomousTradeStore
from trading_agent.kr_social_signal_store import KrSocialSignalStore
from trading_agent.kr_virtual_position_store import KrVirtualPositionStore
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


class KrAutonomousSupervisorStatus(AutonomousSupervisorStatus):
    social_signals: int = Field(ge=0)
    recommendations: int = Field(ge=0)
    no_trade_decisions: int = Field(ge=0)
    open_virtual_positions: int = Field(ge=0)
    terminal_virtual_positions: int = Field(ge=0)
    broker_mutation: Literal[0] = 0
    trading_mutation: Literal[0] = 0


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
) -> AutonomousSupervisorStatus | KrAutonomousSupervisorStatus:
    tasks = AutonomousTaskStore(autonomous_supervisor_paths(config).task_database)
    try:
        status = autonomous_supervisor_status(tasks, now)
        if config.schema_version != 4:
            return status
        return _kr_status(config, status, tuple(task.task_id for task in tasks.reader().tasks()))
    finally:
        tasks.close()


def _kr_status(
    config: ResearchAgentServiceConfig,
    status: AutonomousSupervisorStatus,
    task_ids: tuple[AutonomousTaskId, ...],
) -> KrAutonomousSupervisorStatus:
    signal_database = config.kr_social_signal_database
    if signal_database is None:
        raise AssertionError("validated schema v4 has a social signal database")
    kr_root = config.output_root / "autonomous-supervisor" / "kr-v1"
    trades = KrAutonomousTradeStore(kr_root / "kr-autonomous-trades.sqlite3").events()
    position_events = KrVirtualPositionStore(kr_root / "kr-virtual-positions.sqlite3").all_events()
    positions = tuple({event.position_id: event for event in position_events}.values())
    signal_task_ids = tuple(sorted({*task_ids, *(event.task_id for event in trades)}))
    return KrAutonomousSupervisorStatus(
        **status.model_dump(mode="python"),
        social_signals=sum(
            len(KrSocialSignalStore(signal_database).signals_for_task(task_id)) for task_id in signal_task_ids
        ),
        recommendations=sum(event.outcome is KrAutonomousTradeOutcome.RECOMMEND for event in trades),
        no_trade_decisions=sum(event.outcome is KrAutonomousTradeOutcome.NO_TRADE for event in trades),
        open_virtual_positions=sum(not event.terminal for event in positions),
        terminal_virtual_positions=sum(event.terminal for event in positions),
    )


__all__ = (
    "AutonomousSupervisorPaths",
    "AutonomousSupervisorStatus",
    "KrAutonomousSupervisorStatus",
    "autonomous_supervisor_paths",
    "autonomous_supervisor_status",
    "autonomous_supervisor_status_for_config",
)
