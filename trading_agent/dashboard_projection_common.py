from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from typing import Literal, assert_never

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceStateName,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_receipts import (
    MAX_ITEM_COUNT,
    FailureReason,
    ReceiptRead,
    ReceiptSourceFailure,
    WorkspaceName,
)

TerminalKind = Literal[
    "source_receipt",
    "reviewer_decision",
    "lifecycle_decision",
    "paper_receipt",
    "process_receipt",
    "deployment_receipt",
]
TraceKind = Literal[
    "source_receipt",
    "reviewer_decision",
    "lifecycle_decision",
    "paper_receipt",
    "process_receipt",
    "deployment_receipt",
    "blocker_terminal",
]
TraceState = Literal["accepted", "blocked", "unavailable", "failed"]


@dataclass(frozen=True, slots=True)
class WorkspaceProjection:
    workspace: SourceStateV2
    nodes: tuple[TraceNodeV2, ...]
    edges: tuple[TraceEdgeV2, ...]


def missing_projection(name: WorkspaceName, now: dt.datetime) -> WorkspaceProjection:
    return blocked_projection(
        name,
        now=now,
        state="unavailable",
        blocker_code=f"{name}_authority_missing",
    )


def blocked_projection(
    name: WorkspaceName,
    *,
    now: dt.datetime,
    state: Literal["error", "blocked", "unavailable", "corrupt"],
    blocker_code: str,
) -> WorkspaceProjection:
    source_id = f"trace.{name}.source"
    terminal_id = f"trace.{name}.blocker"
    safe_ref = hashlib.sha256(f"{name}:{blocker_code}".encode()).hexdigest()
    nodes = (
        _node(source_id, "source_receipt", name, now, safe_ref, "unavailable"),
        _node(terminal_id, "blocker_terminal", name, now, safe_ref, "blocked"),
    )
    return WorkspaceProjection(
        workspace=SourceStateV2(
            state=state,
            observed_at=None if state == "unavailable" else now,
            freshness=FreshnessV2(policy_id=f"{name}-receipt-v2", age_seconds=None, as_of=now),
            blocker_code=blocker_code,
            summary=f"{name} authority unavailable",
            total_count=0,
            projected_count=0,
            truncated=False,
            trace_id=source_id,
            items=(),
        ),
        nodes=nodes,
        edges=(TraceEdgeV2(from_node_id=source_id, to_node_id=terminal_id, kind="blocked_by"),),
    )


def receipt_projection(
    name: WorkspaceName,
    result: ReceiptRead | ReceiptSourceFailure,
    *,
    now: dt.datetime,
) -> WorkspaceProjection:
    match result:
        case ReceiptSourceFailure(reason=reason):
            state, suffix = _failure_state(reason)
            return blocked_projection(
                name,
                now=now,
                state=state,
                blocker_code=f"{name}_{suffix}",
            )
        case ReceiptRead(receipts=receipts, observed_at=observed_at, safe_ref=safe_ref):
            blocked = next(
                (
                    receipt
                    for receipt in receipts
                    if receipt.state in {"error", "blocked", "unavailable", "corrupt"}
                ),
                None,
            )
            if blocked is not None:
                match blocked.state:
                    case "error" | "blocked" | "unavailable" | "corrupt" as blocked_state:
                        pass
                    case "loading" | "empty" | "stale" | "populated":
                        return blocked_projection(
                            name,
                            now=now,
                            state="corrupt",
                            blocker_code=f"{name}_receipt_state_invalid",
                        )
                    case unreachable:
                        assert_never(unreachable)
                return blocked_projection(
                    name,
                    now=now,
                    state=blocked_state,
                    blocker_code=blocked.blocker_code or f"{name}_receipt_blocked",
                )
            source_id = f"trace.{name}.source"
            terminal_id = f"trace.{name}.terminal"
            age = max(0, int((now - observed_at).total_seconds()))
            stale = age > 7 * 86_400
            projected_receipts = tuple(receipt for receipt in receipts if receipt.state != "empty")
            items = tuple(
                WorkspaceItemV2(
                    item_id=receipt.item_id,
                    kind=receipt.kind,
                    label=receipt.label,
                    state="stale" if stale else receipt.state,
                    value=receipt.value,
                    observed_at=receipt.observed_at,
                    trace_id=source_id,
                )
                for receipt in projected_receipts[:MAX_ITEM_COUNT]
            )
            total = len(projected_receipts)
            state = _aggregate_state(tuple(item.state for item in items), stale=stale)
            terminal_kind = _terminal_kind(name, receipts[0].terminal_kind)
            nodes = (
                _node(source_id, "source_receipt", name, observed_at, safe_ref, "accepted"),
                _node(terminal_id, terminal_kind, name, observed_at, safe_ref, "accepted"),
            )
            return WorkspaceProjection(
                workspace=SourceStateV2(
                    state=state,
                    observed_at=observed_at,
                    freshness=FreshnessV2(
                        policy_id=f"{name}-receipt-v2",
                        age_seconds=min(age, 31_536_000),
                        as_of=now,
                    ),
                    blocker_code=None,
                    summary=f"{name} projected from accepted receipts",
                    total_count=total,
                    projected_count=len(items),
                    truncated=total > len(items),
                    trace_id=source_id,
                    items=items,
                ),
                nodes=nodes,
                edges=(TraceEdgeV2(from_node_id=source_id, to_node_id=terminal_id, kind="derived_from"),),
            )
        case unreachable:
            assert_never(unreachable)


def _failure_state(
    reason: FailureReason,
) -> tuple[Literal["unavailable", "corrupt"], str]:
    match reason:
        case "missing":
            return "unavailable", "authority_missing"
        case "permissions":
            return "corrupt", "source_permissions_invalid"
        case "invalid":
            return "corrupt", "receipt_invalid"
        case "future":
            return "corrupt", "future_observation"
        case "mixed_epoch":
            return "corrupt", "mixed_snapshot_epoch"
        case "forbidden":
            return "corrupt", "forbidden_content"
        case unreachable:
            assert_never(unreachable)


def _aggregate_state(states: tuple[SourceStateName, ...], *, stale: bool) -> SourceStateName:
    if stale:
        return "stale"
    for candidate in ("corrupt", "error", "blocked", "unavailable", "stale", "populated"):
        if candidate in states:
            return candidate
    return "empty"


def _terminal_kind(name: WorkspaceName, requested: TerminalKind) -> TerminalKind:
    allowed: dict[WorkspaceName, tuple[TerminalKind, ...]] = {
        "command_center": ("process_receipt",),
        "overview": ("source_receipt",),
        "markets": ("source_receipt", "reviewer_decision"),
        "data_sources": ("source_receipt", "reviewer_decision"),
        "research": ("reviewer_decision",),
        "strategies": ("reviewer_decision", "lifecycle_decision"),
        "derivatives": ("source_receipt", "reviewer_decision"),
        "paper": ("paper_receipt",),
        "system": ("reviewer_decision", "process_receipt", "deployment_receipt"),
    }
    return requested if requested in allowed[name] else allowed[name][0]


def _node(
    node_id: str,
    kind: TraceKind,
    namespace: str,
    observed_at: dt.datetime,
    safe_ref: str,
    state: TraceState,
) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label=f"{namespace} evidence",
        observed_at=observed_at,
        safe_ref=safe_ref,
        state=state,
        source_namespace=f"dashboard.{namespace}",
    )
