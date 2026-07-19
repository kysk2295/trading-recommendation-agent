from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import override

from trading_agent.us_runtime_fleet_cycle_cli_result import LIVE_DISABLED, LIVE_NOT_ATTEMPTED
from trading_agent.us_runtime_supervisor_live_audit import RuntimeSupervisorLiveOutcome


class RuntimeMinuteSupervisorError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime minute supervisor input is invalid"


class RuntimeSupervisorOperationBlockedError(RuntimeError):
    __slots__ = ("live_outcome",)

    def __init__(self, live_outcome: RuntimeSupervisorLiveOutcome = LIVE_NOT_ATTEMPTED) -> None:
        super().__init__()
        self.live_outcome = live_outcome

    @override
    def __str__(self) -> str:
        return "runtime supervisor operation is blocked"


class RuntimeSupervisorStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class RuntimeMinuteSupervisorConfig:
    cycles: int
    interval_seconds: float


@dataclass(frozen=True, slots=True)
class RuntimeSupervisorOperationResult:
    fleet_cycle_id: str
    ready: bool
    live_outcome: RuntimeSupervisorLiveOutcome = LIVE_DISABLED


@dataclass(frozen=True, slots=True)
class RuntimeMinuteSupervisorRecord:
    attempt_id: str
    cycle_index: int
    started_at: dt.datetime
    finished_at: dt.datetime
    status: RuntimeSupervisorStatus
    reason: str | None
    fleet_cycle_id: str | None


__all__ = (
    "RuntimeMinuteSupervisorConfig",
    "RuntimeMinuteSupervisorError",
    "RuntimeMinuteSupervisorRecord",
    "RuntimeSupervisorOperationBlockedError",
    "RuntimeSupervisorOperationResult",
    "RuntimeSupervisorStatus",
)
