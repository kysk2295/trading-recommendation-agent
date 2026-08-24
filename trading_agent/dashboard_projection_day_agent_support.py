from __future__ import annotations

import datetime as dt
import hashlib
from typing import Literal, override

from trading_agent.dashboard_models_v2 import SourceStateName, TraceEdgeV2, TraceNodeV2, WorkspaceItemV2
from trading_agent.dashboard_outbound_redaction import redact_outbound_text

FacadeState = Literal["populated", "unavailable", "corrupt"]


class InvalidKrDayLifecycleProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day dashboard lifecycle projection is invalid"


def day_agent_item(
    item_id: str,
    label: str,
    state: SourceStateName,
    value: str,
    observed_at: dt.datetime | None,
    *,
    kind: Literal["research", "day_theme", "day_recommendation"] = "research",
    trace_id: str | None = None,
) -> WorkspaceItemV2:
    return WorkspaceItemV2(
        item_id=item_id,
        kind=kind,
        label=label,
        state=state,
        value=redact_outbound_text(value, max_chars=160),
        observed_at=observed_at,
        trace_id=trace_id or f"trace.{item_id}",
    )


def day_agent_trace_graph(
    items: tuple[WorkspaceItemV2, ...], now: dt.datetime
) -> tuple[tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    nodes: list[TraceNodeV2] = []
    edges: list[TraceEdgeV2] = []
    for item in items:
        safe_ref = hashlib.sha256(f"{item.item_id}:{item.value}".encode()).hexdigest()
        terminal = f"{item.trace_id}.terminal"
        blocked = item.state in {"blocked", "unavailable", "corrupt", "error"}
        observed_at = item.observed_at or now
        nodes.extend(
            (
                TraceNodeV2(
                    node_id=item.trace_id,
                    kind="source_receipt",
                    label=item.label,
                    observed_at=observed_at,
                    safe_ref=safe_ref,
                    state="unavailable" if blocked else "accepted",
                    source_namespace="dashboard.day_agent",
                ),
                TraceNodeV2(
                    node_id=terminal,
                    kind="blocker_terminal" if blocked else "reviewer_decision",
                    label=f"{item.label} projection",
                    observed_at=observed_at,
                    safe_ref=safe_ref,
                    state="blocked" if blocked else "accepted",
                    source_namespace="dashboard.day_agent",
                ),
            )
        )
        edges.append(
            TraceEdgeV2(
                from_node_id=item.trace_id,
                to_node_id=terminal,
                kind="blocked_by" if blocked else "reviewed_by",
            )
        )
    return tuple(nodes), tuple(edges)


__all__ = (
    "FacadeState",
    "InvalidKrDayLifecycleProjectionError",
    "day_agent_item",
    "day_agent_trace_graph",
)
