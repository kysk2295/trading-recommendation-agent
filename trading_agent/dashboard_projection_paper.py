from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_paper_finalized_terminal import (
    FinalizedPaperAuthorityFailure,
    read_finalized_paper_authority,
)
from trading_agent.dashboard_paper_lifecycle import project_paper_lifecycle
from trading_agent.dashboard_projection_common import (
    WorkspaceProjection,
    blocked_projection,
)
from trading_agent.lane_contract_keys import lane_daily_snapshot_key, lane_manifest_key
from trading_agent.lane_policy_models import LaneId, LaneOrderAuthority
from trading_agent.lane_registry_store import (
    InvalidLaneRegistrySourceError,
    LaneRegistryReader,
    UnsupportedLaneRegistrySchemaError,
)


def project_finalized_paper(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    path = outputs / "lane_control" / "lane_registry.sqlite3"
    wal_path = path.with_name(f"{path.name}-wal")
    if wal_path.exists() and wal_path.stat().st_size > 0:
        return blocked_projection(
            "paper",
            now=now,
            state="corrupt",
            blocker_code="paper_source_wal_active",
        )
    try:
        reader = LaneRegistryReader(path)
        manifests = {
            str(item.manifest_key): item.manifest
            for item in reader.manifests()
            if item.manifest.execution_policy.order_authority
            in (LaneOrderAuthority.ALPACA_PAPER, LaneOrderAuthority.SHADOW_ONLY)
        }
        candidates = tuple(
            item
            for item in reader.daily_snapshots()
            if item.snapshot.lane_id in (LaneId.INTRADAY_MOMENTUM, LaneId.SWING_MOMENTUM)
        )
        if any(
            item.snapshot_key != lane_daily_snapshot_key(item.snapshot)
            or item.snapshot.manifest_key not in manifests
            or manifests[item.snapshot.manifest_key].lane_id is not item.snapshot.lane_id
            or lane_manifest_key(manifests[item.snapshot.manifest_key]) != item.snapshot.manifest_key
            for item in candidates
        ):
            raise ValueError
        latest_session = max(
            (item.snapshot.session_date for item in candidates),
            default=None,
        )
        stored_snapshots = tuple(item for item in candidates if item.snapshot.session_date == latest_session)
        if len(stored_snapshots) > 1:
            raise ValueError
        snapshots = tuple(item.snapshot for item in stored_snapshots)
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
    if not (outputs / "paper" / "execution.sqlite3").is_file():
        return blocked_projection(
            "paper",
            now=now,
            state="unavailable",
            blocker_code="paper_finalized_execution_missing",
        )
    lifecycle = project_paper_lifecycle(outputs, latest)
    if lifecycle.blocker_code is not None:
        return blocked_projection(
            "paper",
            now=now,
            state="corrupt" if lifecycle.state == "corrupt" else "blocked",
            blocker_code=lifecycle.blocker_code,
        )
    authority = read_finalized_paper_authority(outputs, latest, now)
    if isinstance(authority, FinalizedPaperAuthorityFailure):
        return blocked_projection(
            "paper",
            now=now,
            state=authority.state,
            blocker_code=authority.blocker_code,
        )
    source_id = "trace.paper.source"
    terminal_id = "trace.paper.finalized"
    age = max(0, int((now - latest.finalized_at).total_seconds()))
    stale = age > 3 * 86_400
    item_state = "stale" if stale else "populated"
    values = (
        ("paper.daily_pnl", "Finalized daily PnL", latest.realized_pnl + latest.unrealized_pnl),
        ("paper.realized_pnl", "Finalized realized PnL", latest.realized_pnl),
        ("paper.unrealized_pnl", "Finalized unrealized PnL", latest.unrealized_pnl),
        ("paper.equity", "Finalized conservative equity", latest.conservative_equity),
        ("paper.planned_open_risk", "Finalized planned open risk", latest.planned_open_risk),
    )
    items = (
        *(
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
        ),
        WorkspaceItemV2(
            item_id="paper.positions",
            kind="paper",
            label="Finalized positions",
            state="empty" if latest.open_position_count == 0 else item_state,
            value=f"{latest.open_position_count} records",
            observed_at=latest.finalized_at,
            trace_id=source_id,
        ),
        WorkspaceItemV2(
            item_id="paper.orders",
            kind="paper",
            label="Finalized open orders",
            state="empty" if latest.open_order_count == 0 else item_state,
            value=f"{latest.open_order_count} records",
            observed_at=latest.finalized_at,
            trace_id=source_id,
        ),
        *lifecycle.items,
    )
    source_ref = str(lane_daily_snapshot_key(latest))
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
            observed_at=authority.receipt.observed_at,
            safe_ref=authority.safe_ref,
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
