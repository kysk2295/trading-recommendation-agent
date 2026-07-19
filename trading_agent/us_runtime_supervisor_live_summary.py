from __future__ import annotations

from dataclasses import dataclass
from typing import override

from trading_agent.us_runtime_minute_supervisor_store import RuntimeMinuteSupervisorStore
from trading_agent.us_runtime_supervisor_live_audit import RuntimeSupervisorLiveStatus


class RuntimeSupervisorLiveSummaryError(ValueError):
    @override
    def __str__(self) -> str:
        return "runtime supervisor live summary is blocked"


@dataclass(frozen=True, slots=True)
class RuntimeSupervisorLiveSummary:
    parent_count: int
    legacy_parent_count: int
    child_count: int
    disabled_count: int
    not_attempted_count: int
    completed_count: int
    blocked_count: int
    selected_count: int
    created_count: int
    replay_count: int


def summarize_runtime_supervisor_live_audit(
    store: RuntimeMinuteSupervisorStore,
) -> RuntimeSupervisorLiveSummary:
    try:
        if type(store) is not RuntimeMinuteSupervisorStore:
            raise RuntimeSupervisorLiveSummaryError
        parents = store.records()
        children = store.live_records()
        legacy_count = len(parents) - len(children)
        if legacy_count < 0 or tuple(item.attempt_id for item in children) != tuple(
            item.attempt_id for item in parents[legacy_count:]
        ):
            raise RuntimeSupervisorLiveSummaryError
        return RuntimeSupervisorLiveSummary(
            len(parents),
            legacy_count,
            len(children),
            sum(item.status is RuntimeSupervisorLiveStatus.DISABLED for item in children),
            sum(item.status is RuntimeSupervisorLiveStatus.NOT_ATTEMPTED for item in children),
            sum(item.status is RuntimeSupervisorLiveStatus.COMPLETED for item in children),
            sum(item.status is RuntimeSupervisorLiveStatus.BLOCKED for item in children),
            sum(item.selected_count for item in children),
            sum(item.created_count for item in children),
            sum(item.replay_count for item in children),
        )
    except (AttributeError, OSError, TypeError, ValueError):
        raise RuntimeSupervisorLiveSummaryError from None


__all__ = (
    "RuntimeSupervisorLiveSummary",
    "RuntimeSupervisorLiveSummaryError",
    "summarize_runtime_supervisor_live_audit",
)
