from __future__ import annotations

import datetime as dt
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.dashboard_models_v2 import SourceStateName

RECEIPT_FILENAME: Final = "dashboard-receipts.v2.jsonl"
MAX_RECEIPT_BYTES: Final = 256 * 1024
MAX_RECEIPTS: Final = 512
MAX_ITEM_COUNT: Final = 24
_FORBIDDEN = re.compile(
    r"(?i)(api[_-]?key|secret|token|credential|authorization|account[_-]?(id|number|fingerprint)|"
    r"session[_-]?id|request[_-]?header|raw[_-]?(payload|log)|stdout|stderr|"
    r"(?:^|[\s=])/(?:users|home|private|var|tmp)/)"
)

WorkspaceName = Literal[
    "command_center",
    "overview",
    "markets",
    "data_sources",
    "research",
    "strategies",
    "derivatives",
    "paper",
    "system",
]
ItemKind = Literal["metric", "research", "strategy", "derivative", "paper", "system"]
TerminalKind = Literal[
    "source_receipt",
    "reviewer_decision",
    "lifecycle_decision",
    "paper_receipt",
    "process_receipt",
    "deployment_receipt",
]
FailureReason = Literal["missing", "permissions", "invalid", "future", "mixed_epoch", "forbidden"]


class ProjectionReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2]
    snapshot_epoch: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    workspace: WorkspaceName
    item_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    kind: ItemKind
    label: str = Field(min_length=1, max_length=80)
    value: str | None = Field(max_length=160)
    observed_at: dt.datetime
    safe_ref: str = Field(pattern=r"^[a-f0-9]{64}$")
    terminal_kind: TerminalKind
    state: SourceStateName
    blocker_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{3,80}$")

    @model_validator(mode="after")
    def validate_receipt(self) -> ProjectionReceipt:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise InvalidProjectionReceiptError(reason="naive_observation")
        blocked = self.state in {"error", "blocked", "unavailable", "corrupt"}
        if blocked != (self.blocker_code is not None):
            raise InvalidProjectionReceiptError(reason="blocker_metadata_inconsistent")
        if any(
            _FORBIDDEN.search(text) is not None
            for text in (self.snapshot_epoch, self.item_id, self.label, self.value or "")
        ):
            raise ForbiddenProjectionContentError
        return self


@dataclass(frozen=True, slots=True)
class ReceiptRead:
    receipts: tuple[ProjectionReceipt, ...]
    observed_at: dt.datetime
    safe_ref: str


@dataclass(frozen=True, slots=True)
class ReceiptSourceFailure:
    reason: FailureReason


@dataclass(frozen=True, slots=True)
class InvalidProjectionReceiptError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return self.reason


class ForbiddenProjectionContentError(ValueError):
    @override
    def __str__(self) -> str:
        return "forbidden projection content"


def read_projection_receipts(
    root: Path,
    workspace: WorkspaceName,
    *,
    now: dt.datetime,
) -> ReceiptRead | ReceiptSourceFailure:
    path = root / RECEIPT_FILENAME
    if not root.exists() or not path.exists():
        return ReceiptSourceFailure("missing")
    try:
        root_metadata = root.lstat()
        metadata = path.lstat()
        if (
            root.is_symlink()
            or path.is_symlink()
            or not stat.S_ISDIR(root_metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            return ReceiptSourceFailure("permissions")
        payload = path.read_bytes()
        if not payload or len(payload) > MAX_RECEIPT_BYTES:
            return ReceiptSourceFailure("invalid")
        if _FORBIDDEN.search(payload.decode("utf-8", errors="ignore")) is not None:
            return ReceiptSourceFailure("forbidden")
        lines = payload.splitlines()
        if len(lines) > MAX_RECEIPTS:
            return ReceiptSourceFailure("invalid")
        receipts = tuple(ProjectionReceipt.model_validate_json(line) for line in lines)
    except ForbiddenProjectionContentError:
        return ReceiptSourceFailure("forbidden")
    except (OSError, ValidationError, InvalidProjectionReceiptError):
        return ReceiptSourceFailure("invalid")
    selected = tuple(receipt for receipt in receipts if receipt.workspace == workspace)
    if not selected:
        return ReceiptSourceFailure("missing")
    if len({receipt.item_id for receipt in selected}) != len(selected):
        return ReceiptSourceFailure("invalid")
    if len({receipt.snapshot_epoch for receipt in selected}) != 1:
        return ReceiptSourceFailure("mixed_epoch")
    observed_at = max(receipt.observed_at for receipt in selected)
    if observed_at > now + dt.timedelta(minutes=5):
        return ReceiptSourceFailure("future")
    return ReceiptRead(selected, observed_at, selected[0].safe_ref)


__all__ = (
    "MAX_ITEM_COUNT",
    "RECEIPT_FILENAME",
    "FailureReason",
    "ProjectionReceipt",
    "ReceiptRead",
    "ReceiptSourceFailure",
    "WorkspaceName",
    "read_projection_receipts",
)
