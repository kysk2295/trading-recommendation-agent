from __future__ import annotations

import json

from trading_agent.dashboard_projection_common import WorkspaceProjection


def stable_derivatives_payload(projection: WorkspaceProjection) -> str:
    workspace = projection.workspace
    return json.dumps(
        {
            "blocker_code": workspace.blocker_code,
            "edges": [edge.model_dump(mode="json") for edge in projection.edges],
            "items": [
                {
                    "item_id": item.item_id,
                    "observed_at": None if item.observed_at is None else item.observed_at.isoformat(),
                    "state": item.state,
                    "value": item.value,
                }
                for item in workspace.items
            ],
            "nodes": [
                {
                    "kind": node.kind,
                    "node_id": node.node_id,
                    "safe_ref": node.safe_ref,
                    "source_namespace": node.source_namespace,
                    "state": node.state,
                }
                for node in projection.nodes
            ],
            "observed_at": None if workspace.observed_at is None else workspace.observed_at.isoformat(),
            "projected_count": workspace.projected_count,
            "state": workspace.state,
            "total_count": workspace.total_count,
            "truncated": workspace.truncated,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = ("stable_derivatives_payload",)
