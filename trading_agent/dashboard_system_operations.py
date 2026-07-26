from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal, assert_never

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.dashboard_system_operation_receipts import (
    OPERATIONS_FILE,
    LaunchdReceipt,
    OperationReceipt,
    RailwayReceipt,
    RelayReceipt,
    read_operation_receipts,
)


def project_system_operations(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    read = read_operation_receipts(outputs / "system" / OPERATIONS_FILE, now)
    match read:
        case tuple() as receipts:
            return _project(receipts, now)
        case str() as reason:
            return _invalid(reason, now)
        case unreachable:
            assert_never(unreachable)


def _project(
    receipts: tuple[OperationReceipt, ...],
    now: dt.datetime,
) -> WorkspaceProjection:
    if not receipts:
        return _invalid("system_operations_missing", now, unavailable=True)
    parts = tuple(_item(receipt, now) for receipt in receipts[:50])
    blocker = next((part[3] for part in parts if part[3] is not None), None)
    trace_id = next(
        (part[0].trace_id for part in parts if part[3] is not None),
        parts[0][0].trace_id,
    )
    return WorkspaceProjection(
        SourceStateV2(
            state="blocked" if blocker is not None else "populated",
            observed_at=max(item.observed_at for item in receipts),
            freshness=FreshnessV2(
                policy_id="typed-system-operations-v2",
                age_seconds=0,
                as_of=now,
            ),
            blocker_code=blocker,
            summary="Typed launchd, Railway, and relay evidence projected",
            total_count=len(receipts),
            projected_count=len(parts),
            truncated=len(receipts) > len(parts),
            trace_id=trace_id,
            items=tuple(part[0] for part in parts),
        ),
        tuple(node for part in parts for node in part[1]),
        tuple(edge for part in parts for edge in part[2]),
    )


def _item(
    receipt: OperationReceipt,
    now: dt.datetime,
) -> tuple[
    WorkspaceItemV2,
    tuple[TraceNodeV2, ...],
    tuple[TraceEdgeV2, ...],
    str | None,
]:
    match receipt:
        case LaunchdReceipt():
            stale = receipt.status == "running" and now - receipt.observed_at > dt.timedelta(minutes=5)
            failed = receipt.status == "failed" or (
                receipt.last_exit_code is not None and receipt.last_exit_code != 0
            )
            blocker = "launchd_pid_stale" if stale else "launchd_job_failed" if failed else None
            value = receipt.status
            terminal_kind: Literal["process_receipt", "deployment_receipt"] = "process_receipt"
            label = receipt.job_id
            safe_ref = receipt.receipt_sha256
        case RailwayReceipt():
            mismatch = receipt.code_sha256 != receipt.expected_code_sha256
            blocker = (
                "deployment_sha_mismatch"
                if mismatch
                else "railway_health_failed"
                if receipt.health == "unhealthy" or receipt.service_count != 1
                else None
            )
            value = receipt.health
            terminal_kind = "deployment_receipt"
            label = "Railway deployment"
            safe_ref = receipt.receipt_sha256
        case RelayReceipt():
            stale = now - receipt.observed_at > dt.timedelta(minutes=5)
            blocker = (
                "relay_receipt_stale"
                if stale
                else "publisher_relay_offline"
                if receipt.state == "disconnected"
                else None
            )
            value = receipt.state
            terminal_kind = "process_receipt"
            label = "Event relay"
            safe_ref = receipt.receipt_sha256
        case unreachable:
            assert_never(unreachable)
    source_id = f"trace.system.operation.{receipt.evidence_id}"
    terminal_id = f"{source_id}.terminal"
    nodes = (
        _node(source_id, "source_receipt", receipt.observed_at, safe_ref, "accepted"),
        _node(
            terminal_id,
            "blocker_terminal" if blocker is not None else terminal_kind,
            receipt.observed_at,
            safe_ref,
            "blocked" if blocker is not None else "accepted",
        ),
    )
    return (
        WorkspaceItemV2(
            item_id=f"system.operation.{receipt.evidence_id}",
            kind="system",
            label=label,
            state="blocked" if blocker is not None else "populated",
            value=value,
            observed_at=receipt.observed_at,
            trace_id=source_id,
        ),
        nodes,
        (
            TraceEdgeV2(
                from_node_id=source_id,
                to_node_id=terminal_id,
                kind="blocked_by" if blocker is not None else "executed_as",
            ),
        ),
        blocker,
    )


def _invalid(
    reason: str,
    now: dt.datetime,
    *,
    unavailable: bool = False,
) -> WorkspaceProjection:
    source_id = "trace.system.operations"
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
            _node(source_id, "source_receipt", now, safe_ref, "unavailable"),
            _node(f"{source_id}.blocker", "blocker_terminal", now, safe_ref, "blocked"),
        ),
        (
            TraceEdgeV2(
                from_node_id=source_id,
                to_node_id=f"{source_id}.blocker",
                kind="blocked_by",
            ),
        ),
    )


def _node(
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


__all__ = (
    "OPERATIONS_FILE",
    "project_system_operations",
)
