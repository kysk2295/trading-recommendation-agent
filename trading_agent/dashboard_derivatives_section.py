from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from trading_agent.dashboard_models_v2 import (
    SourceStateName,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)


@dataclass(frozen=True, slots=True)
class DerivativesSection:
    state: SourceStateName
    blocker_code: str | None
    observed_at: dt.datetime | None
    items: tuple[WorkspaceItemV2, ...]
    nodes: tuple[TraceNodeV2, ...]
    edges: tuple[TraceEdgeV2, ...]


__all__ = ("DerivativesSection",)
