from __future__ import annotations

import datetime as dt
import os
import re
import stat
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.dashboard_agent_family import AgentFamilyId
from trading_agent.dashboard_autonomous_research import TriggerType

AUTONOMOUS_CONTROL_FILE: Final = "autonomous-control.v1.jsonl"
CONTROL_COMPONENTS: Final = (
    "scheduler",
    "trigger",
    "claim",
    "budget",
    "cooldown",
    "concurrency",
    "failure_budget",
    "worktree",
    "cleanup",
)
ControlComponent = Literal[
    "scheduler",
    "trigger",
    "claim",
    "budget",
    "cooldown",
    "concurrency",
    "failure_budget",
    "worktree",
    "cleanup",
]
ControlState = Literal[
    "passed",
    "authorized",
    "claimed",
    "running",
    "completed",
    "blocked",
    "failed",
    "uncertain",
]
_FORBIDDEN = re.compile(
    r"(?i)(api[_-]?key|secret|token|credential|authorization|account[_-]?id|"
    r"/users/|/home/|worktree[_-]?id|session[_-]?id|raw[_-]?(?:log|payload|header)|environment)"
)


class AutonomousControlReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1]
    evidence_type: Literal["autonomous_control"]
    evidence_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    component: ControlComponent
    agent_family_id: AgentFamilyId
    trigger_type: TriggerType
    observed_at: AwareDatetime
    state: ControlState
    blocker_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{3,80}$")
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_terminal(self) -> Self:
        blocked = self.state in {"blocked", "failed", "uncertain"}
        if blocked != (self.blocker_code is not None):
            raise ValueError
        return self


def read_autonomous_control_receipts(
    path: Path,
    now: dt.datetime,
) -> tuple[AutonomousControlReceipt, ...] | str:
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
            return "autonomous_control_permissions_invalid"
        payload = path.read_bytes()
        if not payload or len(payload) > 128 * 1024:
            return "autonomous_control_invalid"
        receipts = tuple(
            AutonomousControlReceipt.model_validate_json(line)
            for line in payload.splitlines()
        )
    except (OSError, ValidationError, ValueError):
        return "autonomous_control_invalid"
    if len({receipt.evidence_id for receipt in receipts}) != len(receipts):
        return "autonomous_control_conflict"
    if any(_FORBIDDEN.search(receipt.evidence_id) is not None for receipt in receipts):
        return "autonomous_control_forbidden_content"
    components = tuple(receipt.component for receipt in receipts)
    if components != CONTROL_COMPONENTS:
        return "autonomous_control_components_invalid"
    if len({receipt.agent_family_id for receipt in receipts}) != 1:
        return "autonomous_control_family_conflict"
    if any(receipt.observed_at > now + dt.timedelta(minutes=5) for receipt in receipts):
        return "autonomous_control_future"
    return receipts


__all__ = (
    "AUTONOMOUS_CONTROL_FILE",
    "CONTROL_COMPONENTS",
    "AutonomousControlReceipt",
    "read_autonomous_control_receipts",
)
