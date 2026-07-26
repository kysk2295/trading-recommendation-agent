from __future__ import annotations

import datetime as dt
from typing import Literal

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection


def system_operation_node(
    node_id: str,
    kind: Literal[
        "source_receipt",
        "process_receipt",
        "deployment_receipt",
        "blocker_terminal",
    ],
    observed_at: dt.datetime,
    safe_ref: str,
    state: Literal["accepted", "blocked", "unavailable"],
) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label="Typed system operation evidence",
        observed_at=observed_at,
        safe_ref=safe_ref,
        state=state,
        source_namespace="system.operations",
    )


def invalid_system_operations_projection(
    reason: str,
    now: dt.datetime,
    *,
    unavailable: bool,
) -> WorkspaceProjection:
    source_id = "trace.system.operations"
    terminal_id = f"{source_id}.blocker"
    safe_ref = "0" * 64
    return WorkspaceProjection(
        SourceStateV2(
            state="unavailable" if unavailable else "corrupt",
            observed_at=None if unavailable else now,
            freshness=FreshnessV2(
                policy_id="typed-system-operations-v2",
                age_seconds=None,
                as_of=now,
            ),
            blocker_code=reason,
            summary="Typed system operations evidence unavailable",
            total_count=0,
            projected_count=0,
            truncated=False,
            trace_id=source_id,
            items=(),
        ),
        (
            system_operation_node(
                source_id,
                "source_receipt",
                now,
                safe_ref,
                "unavailable",
            ),
            system_operation_node(
                terminal_id,
                "blocker_terminal",
                now,
                safe_ref,
                "blocked",
            ),
        ),
        (
            TraceEdgeV2(
                from_node_id=source_id,
                to_node_id=terminal_id,
                kind="blocked_by",
            ),
        ),
    )


__all__ = (
    "invalid_system_operations_projection",
    "system_operation_node",
)
