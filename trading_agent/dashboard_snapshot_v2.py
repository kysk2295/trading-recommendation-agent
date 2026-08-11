from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Final, override

from trading_agent.dashboard_agent_runtime import (
    InvalidAgentRuntimeReceiptError,
    project_agent_runtime,
)
from trading_agent.dashboard_derivatives_options import OPTIONS_TRACE_ID
from trading_agent.dashboard_market_calendar import project_market_calendar
from trading_agent.dashboard_models_v2 import (
    CommandCenterV2,
    DashboardSnapshotV2,
    DataSourcesV2,
    DerivativesWorkspaceV2,
    ProjectionMetadataV2,
    TraceGraphV2,
    WorkspacesV2,
)
from trading_agent.dashboard_options_workbench_projection import project_options_workbench
from trading_agent.dashboard_projection_common import (
    WorkspaceProjection,
    receipt_projection,
)
from trading_agent.dashboard_projection_derivatives import project_derivatives
from trading_agent.dashboard_projection_experiments import (
    project_research,
    project_strategies,
)
from trading_agent.dashboard_projection_paper import project_finalized_paper
from trading_agent.dashboard_projection_receipts import (
    WorkspaceName,
    read_projection_receipts,
)
from trading_agent.dashboard_projection_sources import project_data_sources
from trading_agent.dashboard_session_terminals import project_session_terminals
from trading_agent.dashboard_system_current_authority import (
    SystemAuthorityVerifierInput,
)
from trading_agent.dashboard_system_evidence import project_system_evidence

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


class DashboardSnapshotV2TimeError(ValueError):
    @override
    def __str__(self) -> str:
        return "dashboard v2 snapshot time must be timezone-aware"


def collect_dashboard_snapshot_v2(
    outputs: Path,
    *,
    now: dt.datetime | None = None,
    system_authority_verifier: SystemAuthorityVerifierInput = None,
    cycle_database: Path | None = None,
) -> DashboardSnapshotV2:
    generated_at = dt.datetime.now(dt.UTC) if now is None else now
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise DashboardSnapshotV2TimeError
    projections: dict[WorkspaceName, WorkspaceProjection] = {
        name: receipt_projection(
            name,
            read_projection_receipts(outputs / root, name, now=generated_at),
            now=generated_at,
        )
        for name, root in ROOT_BY_WORKSPACE.items()
        if name
        not in {
            "overview",
            "markets",
            "data_sources",
            "research",
            "strategies",
            "derivatives",
            "paper",
            "system",
        }
    }
    projections["overview"] = project_market_calendar(outputs, now=generated_at, workspace="overview")
    projections["markets"] = project_session_terminals(
        project_market_calendar(outputs, now=generated_at, workspace="markets"),
        outputs,
        now=generated_at,
    )
    sources_projection, capabilities = project_data_sources(outputs, now=generated_at)
    projections["data_sources"] = sources_projection
    projections["research"] = project_research(outputs, now=generated_at)
    projections["strategies"] = project_strategies(outputs, now=generated_at)
    projections["derivatives"] = project_derivatives(outputs, now=generated_at)
    projections["paper"] = _paper_projection(outputs, generated_at)
    projections["system"] = project_system_evidence(
        outputs,
        now=generated_at,
        authority_verifier=system_authority_verifier,
    )
    try:
        agent_projection, agents = project_agent_runtime(
            outputs,
            now=generated_at,
            cycle_database=cycle_database,
        )
    except InvalidAgentRuntimeReceiptError:
        agent_projection, agents = project_agent_runtime(
            outputs / ".invalid-agent-runtime",
            now=generated_at,
        )
    projections["command_center"] = agent_projection
    command = agent_projection.workspace
    sources = projections["data_sources"].workspace
    derivatives = projections["derivatives"].workspace
    workspaces = WorkspacesV2(
        command_center=CommandCenterV2(
            **command.model_dump(),
            agents=agents,
        ),
        overview=projections["overview"].workspace,
        markets=projections["markets"].workspace,
        data_sources=DataSourcesV2(
            **sources.model_dump(),
            capabilities=capabilities,
        ),
        research=projections["research"].workspace,
        strategies=projections["strategies"].workspace,
        derivatives=DerivativesWorkspaceV2(
            **derivatives.model_dump(),
            workbench=project_options_workbench(
                outputs=outputs,
                now=generated_at,
                derivatives_trace_id=OPTIONS_TRACE_ID,
                agent_workspace=agent_projection.workspace,
                research_workspace=projections["research"].workspace,
                strategies_workspace=projections["strategies"].workspace,
            ),
        ),
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
                "alpaca-options-reader-v1",
                "cftc-tff-reader-v1",
                "dashboard-receipt-reader-v2",
                "experiment-ledger-reader-v1",
                "fred-alfred-artifact-reader-v1",
                "futures-security-master-reader-v1",
                "kr-theme-provider-reader-v1",
                "market-calendar-reader-v2",
                "hermes-session-terminal-reader-v1",
                "lane-registry-reader-v1",
                "system-milestone-reader-v2",
                "system-current-authority-reader-v2",
                "system-autonomous-control-reader-v2",
                "system-operations-reader-v2",
                "treasury-yield-reader-v1",
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


__all__ = ("DashboardSnapshotV2TimeError", "collect_dashboard_snapshot_v2")
