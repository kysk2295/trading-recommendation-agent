from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Final, override

from trading_agent.dashboard_models_v2 import (
    CommandCenterV2,
    DashboardSnapshotV2,
    DataSourcesV2,
    ProjectionMetadataV2,
    SourceCapabilityV2,
    TraceGraphV2,
    WorkspacesV2,
)
from trading_agent.dashboard_projection_common import (
    WorkspaceProjection,
    receipt_projection,
)
from trading_agent.dashboard_projection_paper import project_finalized_paper
from trading_agent.dashboard_projection_receipts import (
    WorkspaceName,
    read_projection_receipts,
)

ROOT_BY_WORKSPACE: Final[dict[WorkspaceName, str]] = {
    "command_center": "system",
    "overview": "live_sessions",
    "markets": "live_sessions",
    "data_sources": "source_evidence",
    "research": "experiment_control",
    "strategies": "lane_control",
    "derivatives": "derivatives",
    "paper": "paper",
    "system": "system",
}
PROVIDERS: Final = ("fred", "alfred", "treasury", "cftc", "opendart", "kis", "ls", "alpaca")


class DashboardSnapshotV2TimeError(ValueError):
    @override
    def __str__(self) -> str:
        return "dashboard v2 snapshot time must be timezone-aware"


def collect_dashboard_snapshot_v2(
    outputs: Path,
    *,
    now: dt.datetime | None = None,
) -> DashboardSnapshotV2:
    generated_at = dt.datetime.now(dt.UTC) if now is None else now
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise DashboardSnapshotV2TimeError
    projections = {
        name: receipt_projection(
            name,
            read_projection_receipts(outputs / root, name, now=generated_at),
            now=generated_at,
        )
        for name, root in ROOT_BY_WORKSPACE.items()
        if name != "paper"
    }
    projections["paper"] = _paper_projection(outputs, generated_at)
    command = projections["command_center"].workspace
    sources = projections["data_sources"].workspace
    workspaces = WorkspacesV2(
        command_center=CommandCenterV2(**command.model_dump(), agents=()),
        overview=projections["overview"].workspace,
        markets=projections["markets"].workspace,
        data_sources=DataSourcesV2(
            **sources.model_dump(),
            capabilities=_capabilities(projections["data_sources"]),
        ),
        research=projections["research"].workspace,
        strategies=projections["strategies"].workspace,
        derivatives=projections["derivatives"].workspace,
        paper=projections["paper"].workspace,
        system=projections["system"].workspace,
    )
    nodes = tuple(node for projection in projections.values() for node in projection.nodes)
    edges = tuple(edge for projection in projections.values() for edge in projection.edges)
    total = sum(projection.workspace.total_count for projection in projections.values())
    projected = sum(projection.workspace.projected_count for projection in projections.values())
    identity = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"dashboard-v2:{generated_at.isoformat()}:{':'.join(node.safe_ref or node.node_id for node in nodes)}",
    )
    return DashboardSnapshotV2(
        snapshot_id=identity,
        generated_at=generated_at,
        workspaces=workspaces,
        traces=TraceGraphV2(nodes=nodes, edges=edges),
        projection=ProjectionMetadataV2(
            redaction_policy_version="dashboard-redaction-v2",
            reader_versions=(
                "dashboard-receipt-reader-v2",
                "lane-registry-reader-v1",
            ),
            source_schema_version=2,
            total_count=total,
            projected_count=projected,
            truncated=total > projected,
        ),
    )


def _paper_projection(outputs: Path, now: dt.datetime) -> WorkspaceProjection:
    ledger = outputs / "lane_control" / "lane_registry.sqlite3"
    if ledger.exists() or ledger.with_name(f"{ledger.name}-wal").exists():
        return project_finalized_paper(outputs, now=now)
    return receipt_projection(
        "paper",
        read_projection_receipts(outputs / ROOT_BY_WORKSPACE["paper"], "paper", now=now),
        now=now,
    )


def _capabilities(projection: WorkspaceProjection) -> tuple[SourceCapabilityV2, ...]:
    workspace = projection.workspace
    return tuple(
        SourceCapabilityV2(
            capability_id=f"{provider}.dashboard",
            provider=provider,
            label=provider.upper(),
            state=workspace.state,
            entitlement="unavailable" if workspace.state == "unavailable" else "research_only",
            observed_at=workspace.observed_at,
            trace_id=workspace.trace_id,
        )
        for provider in PROVIDERS
    )


__all__ = ("DashboardSnapshotV2TimeError", "collect_dashboard_snapshot_v2")
