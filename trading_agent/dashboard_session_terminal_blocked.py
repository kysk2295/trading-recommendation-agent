from __future__ import annotations

import datetime as dt
import hashlib

from trading_agent.dashboard_models_v2 import TraceEdgeV2, TraceNodeV2
from trading_agent.dashboard_projection_common import WorkspaceProjection


def blocked_session_terminal_projection(
    base: WorkspaceProjection,
    now: dt.datetime,
) -> WorkspaceProjection:
    safe_ref = hashlib.sha256(b"invalid-session-terminal-database").hexdigest()
    terminal_id = "trace.markets.session_terminals.blocker"
    workspace = base.workspace.model_copy(
        update={
            "state": "corrupt",
            "observed_at": now,
            "blocker_code": "session_terminal_source_invalid",
            "summary": "Hermes session terminal authority is invalid",
        }
    )
    node = TraceNodeV2(
        node_id=terminal_id,
        kind="blocker_terminal",
        label="Hermes session terminal authority invalid",
        observed_at=now,
        safe_ref=safe_ref,
        state="blocked",
        source_namespace="dashboard.session_terminal",
    )
    edge = TraceEdgeV2(
        from_node_id=base.workspace.trace_id,
        to_node_id=terminal_id,
        kind="blocked_by",
    )
    return WorkspaceProjection(workspace, (*base.nodes, node), (*base.edges, edge))


__all__ = ("blocked_session_terminal_projection",)
