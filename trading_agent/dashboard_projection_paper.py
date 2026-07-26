from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from pathlib import Path

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import (
    WorkspaceProjection,
    blocked_projection,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryReader,
    UnsupportedLaneRegistrySchemaError,
)


def project_finalized_paper(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    path = outputs / "lane_control" / "lane_registry.sqlite3"
    if path.with_name(f"{path.name}-wal").exists():
        return blocked_projection(
            "paper",
            now=now,
            state="corrupt",
            blocker_code="paper_source_wal_active",
        )
    try:
        snapshots = tuple(
            item.snapshot
            for item in LaneRegistryReader(path).daily_snapshots()
            if item.snapshot.lane_id is LaneId.INTRADAY_MOMENTUM
        )
    except (
        InvalidLaneRegistrySourceError,
        OSError,
        sqlite3.Error,
        UnsupportedLaneRegistrySchemaError,
        ValueError,
    ):
        return blocked_projection(
            "paper",
            now=now,
            state="corrupt",
            blocker_code="paper_finalized_ledger_invalid",
        )
    if not snapshots:
        return blocked_projection(
            "paper",
            now=now,
            state="unavailable",
            blocker_code="paper_finalized_ledger_missing",
        )
    latest = max(snapshots, key=lambda item: (item.session_date, item.finalized_at))
    if latest.finalized_at > now + dt.timedelta(minutes=5):
        return blocked_projection(
            "paper",
            now=now,
            state="corrupt",
            blocker_code="paper_future_observation",
        )
    if not latest.data_quality_complete:
        return blocked_projection(
            "paper",
            now=now,
            state="blocked",
            blocker_code="paper_verification_incomplete",
        )
    source_id = "trace.paper.source"
    terminal_id = "trace.paper.finalized"
    age = max(0, int((now - latest.finalized_at).total_seconds()))
    stale = age > 3 * 86_400
    item_state = "stale" if stale else "populated"
    values = (
        ("paper.daily_pnl", "Daily PnL", latest.realized_pnl + latest.unrealized_pnl),
        ("paper.equity", "Conservative equity", latest.conservative_equity),
        ("paper.open_orders", "Open orders", latest.open_order_count),
        ("paper.open_positions", "Open positions", latest.open_position_count),
    )
    items = tuple(
        WorkspaceItemV2(
            item_id=item_id,
            kind="paper",
            label=label,
            state=item_state,
            value=str(value),
            observed_at=latest.finalized_at,
            trace_id=source_id,
        )
        for item_id, label, value in values
    )
    source_ref = hashlib.sha256(latest.source_ledger_sha256.encode()).hexdigest()
    nodes = (
        TraceNodeV2(
            node_id=source_id,
            kind="source_receipt",
            label="Finalized lane ledger",
            observed_at=latest.finalized_at,
            safe_ref=source_ref,
            state="accepted",
            source_namespace="lane.daily_snapshot",
        ),
        TraceNodeV2(
            node_id=terminal_id,
            kind="paper_receipt",
            label="Finalized Paper receipt",
            observed_at=latest.finalized_at,
            safe_ref=latest.source_ledger_sha256,
            state="accepted",
            source_namespace="paper.finalized",
        ),
    )
    return WorkspaceProjection(
        workspace=SourceStateV2(
            state="stale" if stale else "populated",
            observed_at=latest.finalized_at,
            freshness=FreshnessV2(
                policy_id="paper-finalized-session-v2",
                age_seconds=min(age, 31_536_000),
                as_of=now,
            ),
            blocker_code=None,
            summary="Finalized Paper ledger projected",
            total_count=len(items),
            projected_count=len(items),
            truncated=False,
            trace_id=source_id,
            items=items,
        ),
        nodes=nodes,
        edges=(TraceEdgeV2(from_node_id=source_id, to_node_id=terminal_id, kind="reconciled_by"),),
    )
