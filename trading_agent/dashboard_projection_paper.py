from __future__ import annotations

import datetime as dt
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
)
from trading_agent.dashboard_paper_lifecycle import project_paper_lifecycle
from trading_agent.dashboard_projection_common import (
    WorkspaceProjection,
    blocked_projection,
    receipt_projection,
)
from trading_agent.dashboard_projection_receipts import (
    read_projection_receipts,
)
from trading_agent.dashboard_us_day_paper import (
    FinalizedPaperProjectionBundle,
    read_finalized_paper_bundle,
)
from trading_agent.lane_contract_keys import lane_daily_snapshot_key


def project_finalized_paper(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    result = read_finalized_paper_bundle(outputs, now=now)
    if isinstance(result, FinalizedPaperAuthorityFailure):
        return blocked_projection(
            "paper",
            now=now,
            state=result.state,
            blocker_code=result.blocker_code,
        )
    return project_finalized_paper_bundle(outputs, result, now=now)


def project_paper_with_bundle(
    outputs: Path,
    receipt_root: Path,
    *,
    now: dt.datetime,
) -> tuple[WorkspaceProjection, FinalizedPaperProjectionBundle | None]:
    ledger = outputs / "lane_control" / "lane_registry.sqlite3"
    if not (ledger.exists() or ledger.with_name(f"{ledger.name}-wal").exists()):
        return receipt_projection(
            "paper",
            read_projection_receipts(receipt_root, "paper", now=now),
            now=now,
        ), None
    result = read_finalized_paper_bundle(outputs, now=now)
    if isinstance(result, FinalizedPaperAuthorityFailure):
        return blocked_projection(
            "paper",
            now=now,
            state=result.state,
            blocker_code=result.blocker_code,
        ), None
    projection = project_finalized_paper_bundle(outputs, result, now=now)
    day_bundle = (
        result
        if projection.workspace.state in {"populated", "stale"}
        and not isinstance(result.authority, FinalizedPaperAuthorityFailure)
        else None
    )
    return projection, day_bundle


def project_finalized_paper_bundle(
    outputs: Path,
    bundle: FinalizedPaperProjectionBundle,
    *,
    now: dt.datetime,
) -> WorkspaceProjection:
    latest = bundle.snapshot
    lifecycle = project_paper_lifecycle(
        outputs,
        latest,
        bundled_ledger=bundle.ledger,
        bundled_identity=bundle.identity,
    )
    if lifecycle.blocker_code is not None:
        return blocked_projection(
            "paper",
            now=now,
            state="corrupt" if lifecycle.state == "corrupt" else "blocked",
            blocker_code=lifecycle.blocker_code,
        )
    authority = bundle.authority
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


__all__ = ("project_finalized_paper", "project_finalized_paper_bundle", "project_paper_with_bundle")
