from __future__ import annotations

import datetime as dt
import os
import re
import stat
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Final, Literal

from pydantic import Field, TypeAdapter, ValidationError

from trading_agent.dashboard_system_control_models import AutonomousControlReceipt

AUTONOMOUS_CONTROL_FILE: Final = "autonomous-control.v2.jsonl"
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
_FORBIDDEN = re.compile(
    r"(?i)(api[_-]?key|secret|token|credential|authorization|account[_-]?id|"
    r"/users/|/home/|worktree[_-]?id|session[_-]?id|raw[_-]?(?:log|payload|header)|environment)"
)


_ADAPTER = TypeAdapter(
    Annotated[AutonomousControlReceipt, Field(discriminator="component")]
)


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
            _ADAPTER.validate_json(line)
            for line in payload.splitlines()
        )
    except (OSError, ValidationError, ValueError):
        return "autonomous_control_invalid"
    if len({receipt.evidence_id for receipt in receipts}) != len(receipts):
        return "autonomous_control_conflict"
    if any(_FORBIDDEN.search(receipt.evidence_id) is not None for receipt in receipts):
        return "autonomous_control_forbidden_content"
    components = tuple(receipt.component for receipt in receipts)
    for component in CONTROL_COMPONENTS:
        if component not in components:
            return f"autonomous_{component}_missing"
    if components != CONTROL_COMPONENTS:
        return "autonomous_control_components_invalid"
    if len({receipt.agent_family_id for receipt in receipts}) != 1:
        return "autonomous_control_family_conflict"
    if len({receipt.trigger_type for receipt in receipts}) != 1:
        return "autonomous_control_trigger_conflict"
    if any(receipt.observed_at > now + dt.timedelta(minutes=5) for receipt in receipts):
        return "autonomous_control_future"
    if len({receipt.run_id for receipt in receipts}) != 1:
        return "autonomous_control_run_conflict"
    for previous, current in pairwise(receipts):
        if current.previous_receipt_sha256 != previous.receipt_sha256:
            return "autonomous_control_link_mismatch"
        if current.observed_at < previous.observed_at:
            return "autonomous_control_event_order_invalid"
    blocked = False
    for receipt in receipts[:-1]:
        if blocked and receipt.state != "blocked":
            return "autonomous_control_terminal_violation"
        blocked = blocked or receipt.state == "blocked"
    return receipts


__all__ = (
    "AUTONOMOUS_CONTROL_FILE",
    "CONTROL_COMPONENTS",
    "AutonomousControlReceipt",
    "read_autonomous_control_receipts",
)
