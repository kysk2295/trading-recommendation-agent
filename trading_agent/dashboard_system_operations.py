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
    StageReceipt,
    read_operation_receipts,
)
from trading_agent.dashboard_system_trace import (
    invalid_system_operations_projection,
    system_operation_node,
)


def project_system_operations(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    read = read_operation_receipts(outputs / "system" / OPERATIONS_FILE, now)
    match read:
        case tuple() as receipts:
            return _project(receipts, now)
        case str() as reason:
            return invalid_system_operations_projection(
                reason,
                now,
                unavailable=reason.endswith("_missing"),
            )
        case unreachable:
            assert_never(unreachable)


def _project(
    receipts: tuple[OperationReceipt, ...],
    now: dt.datetime,
) -> WorkspaceProjection:
    if not receipts:
        return invalid_system_operations_projection(
            "launchd_receipt_missing",
            now,
            unavailable=True,
        )
    parts = tuple(_item(receipt, now) for receipt in receipts[:13])
    blocker = next((part[3] for part in parts if part[3] is not None), None)
    latest_observation = max(item.observed_at for item in receipts)
    trace_id = next(
        (part[0].trace_id for part in parts if part[3] is not None),
        parts[0][0].trace_id,
    )
    return WorkspaceProjection(
        SourceStateV2(
            state=(
                "unavailable"
                if blocker == "stage_code_unknown"
                else "blocked"
                if blocker is not None
                else "populated"
            ),
            observed_at=latest_observation,
            freshness=FreshnessV2(
                policy_id="typed-system-operations-v2",
                age_seconds=max(0, int((now - latest_observation).total_seconds())),
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
            unverified_exit = (
                receipt.status == "exited"
                and receipt.terminal_receipt_sha256 is None
            )
            blocker = (
                "launchd_pid_stale"
                if stale
                else "launchd_job_failed"
                if failed
                else "launchd_exit_unverified"
                if unverified_exit
                else None
            )
            detail = (
                f" · {receipt.schedule}" if receipt.schedule is not None else ""
            )
            process = (
                " · PID receipt current"
                if receipt.pid is not None and not stale
                else f" · exit {receipt.last_exit_code}"
                if receipt.last_exit_code is not None
                else ""
            )
            value = f"{receipt.status}{detail}{process}"
            terminal_kind: Literal["process_receipt", "deployment_receipt"] = "process_receipt"
            label = receipt.job_id
            safe_ref = receipt.receipt_sha256
            category = "launchd"
        case StageReceipt():
            known_code = receipt.result_code in {
                "stage_passed",
                "stage_failed",
                "stage_blocked",
            }
            blocker = (
                "stage_terminal_missing"
                if receipt.terminal_receipt_sha256 is None
                else "stage_code_unknown"
                if not known_code
                else "stage_failed"
                if receipt.outcome != "passed"
                else None
            )
            value = receipt.result_code
            terminal_kind = "process_receipt"
            label = receipt.stage_id.replace("-", " ").capitalize()
            safe_ref = receipt.receipt_sha256
            category = "stage"
        case RailwayReceipt():
            stale = now - receipt.observed_at > dt.timedelta(minutes=5)
            blocker = (
                "railway_receipt_stale"
                if stale
                else "railway_health_failed"
                if receipt.health != "healthy" or receipt.service_count != 1
                else None
            )
            value = receipt.health
            terminal_kind = "deployment_receipt"
            label = "Railway deployment"
            safe_ref = receipt.receipt_sha256
            category = "railway"
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
            category = "relay"
        case unreachable:
            assert_never(unreachable)
    source_id = f"trace.system.operation.{receipt.evidence_id}"
    terminal_id = f"{source_id}.terminal"
    nodes = (
        system_operation_node(
            source_id,
            "source_receipt",
            receipt.observed_at,
            safe_ref,
            "accepted",
        ),
        system_operation_node(
            terminal_id,
            "blocker_terminal" if blocker is not None else terminal_kind,
            receipt.observed_at,
            safe_ref,
            "blocked" if blocker is not None else "accepted",
        ),
    )
    return (
        WorkspaceItemV2(
            item_id=f"system.operation.{category}.{receipt.evidence_id}",
            kind="system",
            label=label,
            state=(
                "unavailable"
                if blocker in {"stage_code_unknown", "launchd_exit_unverified"}
                else "blocked"
                if blocker is not None
                else "populated"
            ),
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


__all__ = (
    "OPERATIONS_FILE",
    "project_system_operations",
)
