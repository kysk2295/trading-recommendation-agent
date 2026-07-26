from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Final, Literal, assert_never

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.dashboard_system_control_receipts import (
    AUTONOMOUS_CONTROL_FILE,
    AutonomousControlReceipt,
    read_autonomous_control_receipts,
)

_LABELS: Final = {
    "scheduler": "Autonomous scheduler",
    "trigger": "Autonomous trigger",
    "claim": "Autonomous claim",
    "budget": "Budget gate",
    "cooldown": "Cooldown gate",
    "concurrency": "Concurrency gate",
    "failure_budget": "Failure budget",
    "worktree": "Isolated worktree",
    "cleanup": "Cleanup receipt",
}


def project_autonomous_control(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    read = read_autonomous_control_receipts(
        outputs / "system" / AUTONOMOUS_CONTROL_FILE,
        now,
    )
    match read:
        case tuple() as receipts:
            return _project(receipts, now)
        case str() as reason:
            return _invalid(reason, now, corrupt=True)
        case unreachable:
            assert_never(unreachable)


def _project(
    receipts: tuple[AutonomousControlReceipt, ...],
    now: dt.datetime,
) -> WorkspaceProjection:
    if not receipts:
        return _invalid("autonomous_control_missing", now, corrupt=False)
    parts = tuple(_item(receipt) for receipt in receipts)
    blocker = next((part[3] for part in parts if part[3] is not None), None)
    state = "blocked" if blocker is not None else "populated"
    latest_observation = max(receipt.observed_at for receipt in receipts)
    return WorkspaceProjection(
        SourceStateV2(
            state=state,
            observed_at=latest_observation,
            freshness=FreshnessV2(
                policy_id="autonomous-control-receipts-v1",
                age_seconds=max(0, int((now - latest_observation).total_seconds())),
                as_of=now,
            ),
            blocker_code=blocker,
            summary="Typed autonomous control-plane receipts projected",
            total_count=len(parts),
            projected_count=len(parts),
            truncated=False,
            trace_id=next(
                (part[0].trace_id for part in parts if part[3] is not None),
                parts[0][0].trace_id,
            ),
            items=tuple(part[0] for part in parts),
        ),
        tuple(node for part in parts for node in part[1]),
        tuple(edge for part in parts for edge in part[2]),
    )


def _item(
    receipt: AutonomousControlReceipt,
) -> tuple[
    WorkspaceItemV2,
    tuple[TraceNodeV2, ...],
    tuple[TraceEdgeV2, ...],
    str | None,
]:
    blocker = receipt.blocker_code
    source_id = f"trace.system.autonomous.{receipt.component}"
    terminal_id = f"{source_id}.terminal"
    state = "blocked" if blocker is not None else "populated"
    value = blocker or receipt.state
    return (
        WorkspaceItemV2(
            item_id=f"system.autonomous.{receipt.component}",
            kind="system",
            label=_LABELS[receipt.component],
            state=state,
            value=value,
            observed_at=receipt.observed_at,
            trace_id=source_id,
        ),
        (
            _node(source_id, "source_receipt", receipt, "accepted"),
            _node(
                terminal_id,
                "blocker_terminal" if blocker is not None else "process_receipt",
                receipt,
                "blocked" if blocker is not None else "accepted",
            ),
        ),
        (
            TraceEdgeV2(
                from_node_id=source_id,
                to_node_id=terminal_id,
                kind="blocked_by" if blocker is not None else "executed_as",
            ),
        ),
        blocker,
    )


def _invalid(reason: str, now: dt.datetime, *, corrupt: bool) -> WorkspaceProjection:
    source_id = "trace.system.autonomous"
    terminal_id = f"{source_id}.blocker"
    return WorkspaceProjection(
        SourceStateV2(
            state="corrupt" if corrupt else "unavailable",
            observed_at=now if corrupt else None,
            freshness=FreshnessV2(
                policy_id="autonomous-control-receipts-v1",
                age_seconds=None,
                as_of=now,
            ),
            blocker_code=reason,
            summary="Autonomous control-plane authority unavailable",
            total_count=0,
            projected_count=0,
            truncated=False,
            trace_id=source_id,
            items=(),
        ),
        (
            TraceNodeV2(
                node_id=source_id,
                kind="source_receipt",
                label="Autonomous control authority",
                observed_at=now,
                safe_ref="0" * 64,
                state="unavailable",
                source_namespace="system.autonomous",
            ),
            TraceNodeV2(
                node_id=terminal_id,
                kind="blocker_terminal",
                label="Autonomous control blocker",
                observed_at=now,
                safe_ref="0" * 64,
                state="blocked",
                source_namespace="system.autonomous",
            ),
        ),
        (TraceEdgeV2(from_node_id=source_id, to_node_id=terminal_id, kind="blocked_by"),),
    )


def _node(
    node_id: str,
    kind: Literal["source_receipt", "process_receipt", "blocker_terminal"],
    receipt: AutonomousControlReceipt,
    state: Literal["accepted", "blocked"],
) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label=f"{_LABELS[receipt.component]} receipt",
        observed_at=receipt.observed_at,
        safe_ref=receipt.receipt_sha256,
        state=state,
        source_namespace="system.autonomous",
    )


__all__ = ("project_autonomous_control",)
