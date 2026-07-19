from __future__ import annotations

import re

from trading_agent.us_runtime_supervisor_live_audit import RuntimeSupervisorLiveOutcome

_HEX = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)


def runtime_supervisor_outcome_is_valid(
    status: str,
    reason: str | None,
    fleet_cycle_id: str | None,
) -> bool:
    valid_cycle = type(fleet_cycle_id) is str and _HEX.fullmatch(fleet_cycle_id) is not None
    if status == "ready":
        return reason is None and valid_cycle
    if status == "blocked":
        return reason in {"runtime_cycle_blocked", "fleet_gate_blocked"} and (fleet_cycle_id is None or valid_cycle)
    return False


def runtime_supervisor_operation_is_valid(
    fleet_cycle_id: str,
    live_outcome: RuntimeSupervisorLiveOutcome,
) -> bool:
    return _HEX.fullmatch(fleet_cycle_id) is not None and type(live_outcome) is RuntimeSupervisorLiveOutcome


__all__ = (
    "runtime_supervisor_operation_is_valid",
    "runtime_supervisor_outcome_is_valid",
)
