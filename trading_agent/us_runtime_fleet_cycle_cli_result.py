from __future__ import annotations

from dataclasses import dataclass

from trading_agent.us_runtime_live_actionability_dispatch import UsRuntimeLiveActionabilityDispatchResult
from trading_agent.us_runtime_supervisor_live_audit import (
    RuntimeSupervisorLiveOutcome,
    RuntimeSupervisorLiveStatus,
)

LIVE_DISABLED = RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.DISABLED, 0, 0, 0)
LIVE_NOT_ATTEMPTED = RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.NOT_ATTEMPTED, 0, 0, 0)
LIVE_BLOCKED = RuntimeSupervisorLiveOutcome(RuntimeSupervisorLiveStatus.BLOCKED, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class RuntimeFleetCycleCliResult:
    exit_code: int
    live_outcome: RuntimeSupervisorLiveOutcome

    def __post_init__(self) -> None:
        if self.exit_code not in {0, 1} or type(self.live_outcome) is not RuntimeSupervisorLiveOutcome:
            raise ValueError


def completed_live_outcome(
    result: UsRuntimeLiveActionabilityDispatchResult,
) -> RuntimeSupervisorLiveOutcome:
    if type(result) is not UsRuntimeLiveActionabilityDispatchResult:
        raise ValueError
    return RuntimeSupervisorLiveOutcome(
        RuntimeSupervisorLiveStatus.COMPLETED,
        result.selected_count,
        result.created_count,
        result.replay_count,
    )


__all__ = (
    "LIVE_BLOCKED",
    "LIVE_DISABLED",
    "LIVE_NOT_ATTEMPTED",
    "RuntimeFleetCycleCliResult",
    "completed_live_outcome",
)
