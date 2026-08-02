from __future__ import annotations

import datetime as dt
import hashlib
import os
import stat
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from trading_agent.dashboard_agent_cycle_runtime import (
    AgentRuntimeObservation,
    AgentRuntimeState,
    InvalidAgentCycleRuntimeError,
    read_cycle_runtime_observations,
)
from trading_agent.dashboard_agent_family import AGENT_FAMILY_REGISTRY, AgentFamilyId
from trading_agent.dashboard_agent_runtime_projection import build_agent_runtime_projection
from trading_agent.dashboard_models_v2 import PublicAgentViewV2
from trading_agent.dashboard_projection_common import WorkspaceProjection
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_query_file import (
    InvalidPrivateQueryFileError,
    read_private_text_query_only,
)

AgentChannel = Literal["conversation", "directed_tool", "autonomous_research"]
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
                    f"{family.family_id}:{channel}:{state}:{observed_at.isoformat()}:{code_sha256}:{reason or ''}"
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
                _ = publish_private_immutable_text(path, receipt.model_dump_json())
            except InvalidPrivateImmutableFileError as error:
                raise InvalidAgentRuntimeReceiptError from error
            paths.append(path)
    return tuple(paths)


def project_agent_runtime(
    outputs: Path,
    *,
    now: dt.datetime,
    cycle_database: Path | None = None,
) -> tuple[WorkspaceProjection, tuple[PublicAgentViewV2, ...]]:
    if cycle_database is not None and (cycle_database.exists() or cycle_database.is_symlink()):
        try:
            observations = read_cycle_runtime_observations(cycle_database)
        except InvalidAgentCycleRuntimeError:
            raise InvalidAgentRuntimeReceiptError from None
        return build_agent_runtime_projection(
            observations,
            now=now,
            source_namespace="dashboard.agent_runtime.cycle",
        )
    observations = _receipt_observations(_read_receipts(outputs / "system" / "agent-runtime"), now)
    return build_agent_runtime_projection(
        observations,
        now=now,
        source_namespace="dashboard.agent_runtime.receipt",
    )


def _receipt_observations(
    receipts: tuple[AgentRuntimeReceiptV2, ...],
    now: dt.datetime,
) -> tuple[AgentRuntimeObservation, ...]:
    selected: dict[tuple[AgentFamilyId, AgentChannel], AgentRuntimeReceiptV2] = {}
    for receipt in receipts:
        key = (receipt.agent_family_id, receipt.channel)
        current = selected.get(key)
        if current is None or receipt.observed_at > current.observed_at:
            selected[key] = receipt
    observations: list[AgentRuntimeObservation] = []
    for family in AGENT_FAMILY_REGISTRY:
        valid = tuple(
            receipt
            for channel in CHANNELS
            if (receipt := selected.get((family.family_id, channel))) is not None
            and receipt.observed_at <= now + dt.timedelta(minutes=5)
        )
        if len(valid) == len(CHANNELS):
            observations.append(
                AgentRuntimeObservation(
                    family=family.family_id,
                    state=_aggregate(valid),
                    observed_at=max(receipt.observed_at for receipt in valid),
                )
            )
    return tuple(observations)


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
        return tuple(AgentRuntimeReceiptV2.model_validate_json(read_private_text_query_only(path)) for path in paths)
    except (InvalidPrivateQueryFileError, OSError, ValidationError) as error:
        raise InvalidAgentRuntimeReceiptError from error


def _aggregate(receipts: tuple[AgentRuntimeReceiptV2, ...]) -> AgentRuntimeState:
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
