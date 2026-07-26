from __future__ import annotations

import datetime as dt
import os
import re
import stat
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
_FORBIDDEN = re.compile(
    r"(?i)(api[_-]?key|secret|token|credential|authorization|account[_-]?id|/users/)"
)
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


def read_milestone_receipts(
    path: Path,
    now: dt.datetime,
) -> tuple[MilestoneReceipt, ...] | str:
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


__all__ = (
    "MILESTONE_FILE",
    "MILESTONE_IDS",
    "MilestoneReceipt",
    "read_milestone_receipts",
)
