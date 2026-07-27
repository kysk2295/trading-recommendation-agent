from __future__ import annotations

import datetime as dt
import hashlib
import os
import stat
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from trading_agent.dashboard_agent_family import (
    AGENT_FAMILY_REGISTRY,
    AgentFamilyId,
)
from trading_agent.dashboard_models_v2 import (
    FreshnessV2,
    PublicAgentViewV2,
    SourceStateV2,
    TraceEdgeV2,
    TraceNodeV2,
    WorkspaceItemV2,
)
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)

AgentChannel = Literal[
    "conversation",
    "directed_tool",
    "autonomous_research",
]
AgentRuntimeState = Literal[
    "running",
    "armed",
    "idle",
    "failed",
    "unavailable",
]
CHANNELS: tuple[AgentChannel, ...] = (
    "conversation",
    "directed_tool",
    "autonomous_research",
)
MAX_RECEIPTS = 4096


class AgentRuntimeReceiptV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    receipt_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    agent_family_id: AgentFamilyId
    channel: AgentChannel
    state: AgentRuntimeState
    observed_at: AwareDatetime
    code_sha256: str = Field(pattern=r"^[a-f0-9]{40}(?:[a-f0-9]{24})?$")
    reason: str | None = Field(default=None, pattern=r"^[a-z0-9_]{3,80}$")


class InvalidAgentRuntimeReceiptError(RuntimeError):
    pass


def append_agent_runtime_readiness(
    outputs: Path,
    *,
    observed_at: dt.datetime,
    code_sha256: str,
    state: AgentRuntimeState,
    reason: str | None = None,
) -> tuple[Path, ...]:
    root = outputs / "system" / "agent-runtime"
    paths: list[Path] = []
    for family in AGENT_FAMILY_REGISTRY:
        for channel in CHANNELS:
            receipt_id = hashlib.sha256(
                (
                    f"{family.family_id}:{channel}:{state}:"
                    f"{observed_at.isoformat()}:{code_sha256}:{reason or ''}"
                ).encode()
            ).hexdigest()
            receipt = AgentRuntimeReceiptV2(
                receipt_id=receipt_id,
                agent_family_id=family.family_id,
                channel=channel,
                state=state,
                observed_at=observed_at,
                code_sha256=code_sha256,
                reason=reason,
            )
            path = root / f"{receipt_id}.json"
            try:
                _ = publish_private_immutable_text(
                    path,
                    receipt.model_dump_json(),
                )
            except InvalidPrivateImmutableFileError as error:
                raise InvalidAgentRuntimeReceiptError from error
            paths.append(path)
    return tuple(paths)


def project_agent_runtime(
    outputs: Path,
    *,
    now: dt.datetime,
) -> tuple[WorkspaceProjection, tuple[PublicAgentViewV2, ...]]:
    receipts = _read_receipts(outputs / "system" / "agent-runtime")
    selected: dict[tuple[AgentFamilyId, AgentChannel], AgentRuntimeReceiptV2] = {}
    for receipt in receipts:
        key = (receipt.agent_family_id, receipt.channel)
        current = selected.get(key)
        if current is None or receipt.observed_at > current.observed_at:
            selected[key] = receipt
    agents: list[PublicAgentViewV2] = []
    items: list[WorkspaceItemV2] = []
    nodes: list[TraceNodeV2] = []
    edges: list[TraceEdgeV2] = []
    missing = False
    observed: list[dt.datetime] = []
    for family in AGENT_FAMILY_REGISTRY:
        family_receipts = tuple(
            selected.get((family.family_id, channel))
            for channel in CHANNELS
        )
        valid = tuple(
            receipt
            for receipt in family_receipts
            if receipt is not None
            and receipt.observed_at <= now + dt.timedelta(minutes=5)
        )
        runtime_state = _aggregate(valid) if len(valid) == len(CHANNELS) else "unavailable"
        missing = missing or runtime_state == "unavailable"
        trace_id = f"trace.command_center.agent.{family.family_id}.source"
        terminal_id = f"trace.command_center.agent.{family.family_id}.runtime"
        observed_at = max(
            (receipt.observed_at for receipt in valid),
            default=None,
        )
        if observed_at is not None:
            observed.append(observed_at)
        agents.append(
            PublicAgentViewV2(
                agent_id=family.family_id,
                label=family.family_id.replace("_", " ").title(),
                role=family.role,
                capabilities=family.capabilities,
                runtime_state=runtime_state,
                trace_id=trace_id,
            )
        )
        items.append(
            WorkspaceItemV2(
                item_id=f"agent.{family.family_id}",
                kind="system",
                label=family.family_id.replace("_", " ").title(),
                state=(
                    "unavailable"
                    if runtime_state == "unavailable"
                    else "error"
                    if runtime_state == "failed"
                    else "populated"
                ),
                value=runtime_state,
                observed_at=observed_at,
                trace_id=trace_id,
            )
        )
        safe_ref = hashlib.sha256(
            f"{family.family_id}:{runtime_state}".encode()
        ).hexdigest()
        nodes.append(
            TraceNodeV2(
                node_id=trace_id,
                kind="source_receipt",
                label=f"{family.family_id} channel authority",
                observed_at=observed_at or now,
                safe_ref=safe_ref,
                state=(
                    "unavailable"
                    if runtime_state in {"failed", "unavailable"}
                    else "accepted"
                ),
                source_namespace="dashboard.agent_runtime",
            )
        )
        nodes.append(
            TraceNodeV2(
                node_id=terminal_id,
                kind=(
                    "blocker_terminal"
                    if runtime_state in {"failed", "unavailable"}
                    else "process_receipt"
                ),
                label=f"{family.family_id} channel readiness",
                observed_at=observed_at or now,
                safe_ref=safe_ref,
                state=(
                    "blocked"
                    if runtime_state in {"failed", "unavailable"}
                    else "accepted"
                ),
                source_namespace="dashboard.agent_runtime",
            )
        )
        edges.append(
            TraceEdgeV2(
                from_node_id=trace_id,
                to_node_id=terminal_id,
                kind=(
                    "blocked_by"
                    if runtime_state in {"failed", "unavailable"}
                    else "executed_as"
                ),
            )
        )
    workspace_state = "unavailable" if not receipts else "blocked" if missing else "populated"
    root_id = "trace.command_center.runtime"
    root_terminal_id = "trace.command_center.runtime.terminal"
    root_ref = hashlib.sha256(b"command-center-agent-runtime-v2").hexdigest()
    nodes.append(
        TraceNodeV2(
            node_id=root_id,
            kind="source_receipt",
            label="Agent runtime authority",
            observed_at=max(observed, default=now),
            safe_ref=root_ref,
            state=(
                "unavailable"
                if workspace_state != "populated"
                else "accepted"
            ),
            source_namespace="dashboard.agent_runtime",
        )
    )
    nodes.append(
        TraceNodeV2(
            node_id=root_terminal_id,
            kind=(
                "blocker_terminal"
                if workspace_state != "populated"
                else "process_receipt"
            ),
            label="Agent runtime readiness",
            observed_at=max(observed, default=now),
            safe_ref=root_ref,
            state=(
                "blocked"
                if workspace_state != "populated"
                else "accepted"
            ),
            source_namespace="dashboard.agent_runtime",
        )
    )
    edges.append(
        TraceEdgeV2(
            from_node_id=root_id,
            to_node_id=root_terminal_id,
            kind=(
                "blocked_by"
                if workspace_state != "populated"
                else "executed_as"
            ),
        )
    )
    return (
        WorkspaceProjection(
            workspace=SourceStateV2(
                state=workspace_state,
                observed_at=max(observed, default=None),
                freshness=FreshnessV2(
                    policy_id="agent-channel-runtime-v2",
                    age_seconds=(
                        None
                        if not observed
                        else max(0, int((now - max(observed)).total_seconds()))
                    ),
                    as_of=now,
                ),
                blocker_code=(
                    "agent_runtime_missing"
                    if workspace_state == "unavailable"
                    else "agent_channel_missing"
                    if workspace_state == "blocked"
                    else None
                ),
                summary="Exact six-family channel runtime readiness",
                total_count=len(agents),
                projected_count=len(items),
                truncated=False,
                trace_id=root_id,
                items=tuple(items),
            ),
            nodes=tuple(nodes),
            edges=tuple(edges),
        ),
        tuple(agents),
    )


def _read_receipts(root: Path) -> tuple[AgentRuntimeReceiptV2, ...]:
    if not root.exists() and not root.is_symlink():
        return ()
    try:
        metadata = root.lstat()
        paths = tuple(sorted(root.glob("*.json")))
        if (
            root.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or len(paths) > MAX_RECEIPTS
        ):
            raise InvalidAgentRuntimeReceiptError
        return tuple(
            AgentRuntimeReceiptV2.model_validate_json(
                read_private_text_query_only(path)
            )
            for path in paths
        )
    except (
        InvalidPrivateQueryFileError,
        OSError,
        ValidationError,
    ) as error:
        raise InvalidAgentRuntimeReceiptError from error


def _aggregate(
    receipts: tuple[AgentRuntimeReceiptV2, ...],
) -> AgentRuntimeState:
    states = {receipt.state for receipt in receipts}
    if "failed" in states:
        return "failed"
    if "running" in states:
        return "running"
    if "unavailable" in states:
        return "unavailable"
    if states == {"idle"}:
        return "idle"
    return "armed"


__all__ = (
    "AgentRuntimeReceiptV2",
    "InvalidAgentRuntimeReceiptError",
    "append_agent_runtime_readiness",
    "project_agent_runtime",
)
