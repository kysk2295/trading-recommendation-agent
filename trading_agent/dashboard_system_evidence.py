from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection

MILESTONE_FILE: Final = "milestones.v2.jsonl"
MILESTONE_IDS: Final = (
    "M0",
    "M1",
    "M2",
    "M3",
    "M4",
    "M5",
    "M6",
    "M7",
    "M8",
    "M9",
    "M10",
)
_FORBIDDEN = re.compile(r"(?i)(api[_-]?key|secret|token|credential|authorization|account[_-]?id|/users/)")
MilestoneId = Literal["M0", "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10"]
MilestoneStatus = Literal["passed", "failed", "blocked"]


class MilestoneReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2]
    evidence_type: Literal["milestone"]
    epoch_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    milestone_id: MilestoneId
    status: MilestoneStatus
    observed_at: dt.datetime
    code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    result_code: str = Field(pattern=r"^[a-z0-9_]{3,80}$")

    @model_validator(mode="after")
    def validate_receipt(self) -> MilestoneReceipt:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError
        if _FORBIDDEN.search(self.result_code) is not None:
            raise ValueError
        return self


def project_system_evidence(outputs: Path, *, now: dt.datetime) -> WorkspaceProjection:
    result = _read(outputs / "system" / MILESTONE_FILE, now)
    match result:
        case tuple() as receipts:
            return _project(receipts, now)
        case str() as reason:
            return _invalid(reason, now)


def _read(path: Path, now: dt.datetime) -> tuple[MilestoneReceipt, ...] | str:
    if not path.exists():
        return ()
    try:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            return "milestone_source_permissions_invalid"
        payload = path.read_bytes()
        if not payload or len(payload) > 128 * 1024:
            return "milestone_receipt_invalid"
        if _FORBIDDEN.search(payload.decode("utf-8", errors="ignore")) is not None:
            return "milestone_forbidden_content"
        receipts = tuple(MilestoneReceipt.model_validate_json(line) for line in payload.splitlines())
    except (OSError, ValidationError, ValueError):
        return "milestone_receipt_invalid"
    ids = tuple(receipt.milestone_id for receipt in receipts)
    if len(ids) != len(set(ids)):
        return "milestone_receipt_conflict"
    if len({receipt.epoch_id for receipt in receipts}) > 1:
        return "milestone_mixed_epoch"
    if any(receipt.observed_at > now + dt.timedelta(minutes=5) for receipt in receipts):
        return "milestone_future_observation"
    return receipts


def _project(receipts: tuple[MilestoneReceipt, ...], now: dt.datetime) -> WorkspaceProjection:
    by_id = {receipt.milestone_id: receipt for receipt in receipts}
    item_parts = tuple(_milestone_item(milestone_id, by_id.get(milestone_id), now) for milestone_id in MILESTONE_IDS)
    missing = len(receipts) != len(MILESTONE_IDS)
    failed = any(receipt.status != "passed" for receipt in receipts)
    blocked = missing or failed
    root_id = "trace.system.milestones"
    terminal_id = f"{root_id}.terminal"
    safe_ref = hashlib.sha256("".join(MILESTONE_IDS).encode()).hexdigest()
    root_nodes = (
        _node(root_id, "source_receipt", now, safe_ref, "accepted"),
        _node(
            terminal_id,
            "blocker_terminal" if blocked else "process_receipt",
            now,
            safe_ref,
            "blocked" if blocked else "accepted",
        ),
    )
    root_edges = (
        TraceEdgeV2(
            from_node_id=root_id,
            to_node_id=terminal_id,
            kind="blocked_by" if blocked else "executed_as",
        ),
    )
    return WorkspaceProjection(
        SourceStateV2(
            state="blocked" if blocked else "populated",
            observed_at=max((receipt.observed_at for receipt in receipts), default=now),
            freshness=FreshnessV2(policy_id="system-milestones-v2", age_seconds=0, as_of=now),
            blocker_code=(
                "milestone_authority_missing"
                if missing
                else "milestone_failed"
                if failed
                else None
            ),
            summary="M0-M10 typed milestone evidence projected",
            total_count=11,
            projected_count=11,
            truncated=False,
            trace_id=root_id,
            items=tuple(part[0] for part in item_parts),
        ),
        tuple(node for part in item_parts for node in part[1]) + root_nodes,
        tuple(edge for part in item_parts for edge in part[2]) + root_edges,
    )


def _milestone_item(
    milestone_id: str,
    receipt: MilestoneReceipt | None,
    now: dt.datetime,
) -> tuple[WorkspaceItemV2, tuple[TraceNodeV2, ...], tuple[TraceEdgeV2, ...]]:
    source_id = f"trace.system.{milestone_id.lower()}"
    if receipt is None:
        blocker_id = f"{source_id}.blocker"
        safe_ref = hashlib.sha256(f"{milestone_id}:missing".encode()).hexdigest()
        return (
            WorkspaceItemV2(
                item_id=f"system.{milestone_id.lower()}",
                kind="system",
                label=milestone_id,
                state="unavailable",
                value=None,
                observed_at=None,
                trace_id=source_id,
            ),
            (
                _node(source_id, "source_receipt", now, safe_ref, "unavailable"),
                _node(blocker_id, "blocker_terminal", now, safe_ref, "blocked"),
            ),
            (TraceEdgeV2(from_node_id=source_id, to_node_id=blocker_id, kind="blocked_by"),),
        )
    terminal_id = f"{source_id}.terminal"
    safe_ref = hashlib.sha256(receipt.model_dump_json().encode()).hexdigest()
    accepted = receipt.status == "passed"
    return (
        WorkspaceItemV2(
            item_id=f"system.{milestone_id.lower()}",
            kind="system",
            label=milestone_id,
            state="populated" if accepted else "blocked",
            value=receipt.status,
            observed_at=receipt.observed_at,
            trace_id=source_id,
        ),
        (
            _node(source_id, "source_receipt", receipt.observed_at, safe_ref, "accepted"),
            _node(
                terminal_id,
                "process_receipt" if accepted else "blocker_terminal",
                receipt.observed_at,
                receipt.code_sha256,
                "accepted" if accepted else "blocked",
            ),
        ),
        (
            TraceEdgeV2(
                from_node_id=source_id,
                to_node_id=terminal_id,
                kind="executed_as" if accepted else "blocked_by",
            ),
        ),
    )


def _invalid(reason: str, now: dt.datetime) -> WorkspaceProjection:
    projection = _project((), now)
    return WorkspaceProjection(
        projection.workspace.model_copy(
            update={"state": "corrupt", "observed_at": now, "blocker_code": reason}
        ),
        projection.nodes,
        projection.edges,
    )


def _node(
    node_id: str,
    kind: Literal["source_receipt", "process_receipt", "blocker_terminal"],
    observed_at: dt.datetime,
    safe_ref: str,
    state: Literal["accepted", "blocked", "unavailable"],
) -> TraceNodeV2:
    return TraceNodeV2(
        node_id=node_id,
        kind=kind,
        label="Typed system milestone evidence",
        observed_at=observed_at,
        safe_ref=safe_ref,
        state=state,
        source_namespace="system.milestones",
    )
