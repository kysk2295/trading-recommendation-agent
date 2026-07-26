from __future__ import annotations

import datetime as dt
import os
import re
import stat
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

OPERATIONS_FILE: Final = "system-operations.v2.jsonl"
_FORBIDDEN = re.compile(
    r"(?i)(api[_-]?key|secret|token|credential|authorization|account[_-]?id|"
    r"/users/|/home/|worktree[_-]?id|session[_-]?id|raw[_-]?(?:log|payload|header)|environment)"
)
_PRODUCT_IDENTITIES: Final = frozenset(
    {
        "opportunity_manager",
        "day_trading",
        "swing_trading",
        "systematic_quant",
        "derivatives_research",
        "market_context",
        "allocation_manager",
    }
)
OperationalAlias = Literal[
    "kr-theme",
    "us-intraday",
    "us-systematic",
    "us-swing",
    "research",
    "delivery",
]


class LaunchdReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2]
    evidence_type: Literal["launchd"]
    evidence_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    job_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{0,79}$")
    operational_alias: OperationalAlias | None = None
    schedule: Literal["event_driven", "calendar", "manual"] | None = None
    observed_at: AwareDatetime
    status: Literal["scheduled", "running", "exited", "failed"]
    pid: int | None = Field(default=None, ge=1)
    process_started_at: AwareDatetime | None = None
    last_exit_code: int | None = None
    exit_observed_at: AwareDatetime | None = None
    terminal_receipt_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_variant(self) -> Self:
        running = self.status == "running"
        exited = self.status in {"exited", "failed"}
        if self.job_id in _PRODUCT_IDENTITIES or (
            self.operational_alias is not None
            and self.operational_alias in _PRODUCT_IDENTITIES
        ):
            raise ValueError
        if (
            running != (self.pid is not None and self.process_started_at is not None)
            or exited != (self.last_exit_code is not None)
            or exited != (self.exit_observed_at is not None or self.last_exit_code is not None)
            or (
                self.process_started_at is not None
                and self.process_started_at > self.observed_at
            )
            or (
                self.exit_observed_at is not None
                and self.exit_observed_at > self.observed_at
            )
        ):
            raise ValueError
        return self


class StageReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2]
    evidence_type: Literal["stage"]
    evidence_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    run_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    stage_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,80}$")
    observed_at: AwareDatetime
    outcome: Literal["passed", "failed", "blocked"]
    result_code: str = Field(pattern=r"^[a-z0-9_]{3,80}$")
    terminal_receipt_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RailwayReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2]
    evidence_type: Literal["railway"]
    evidence_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    deployment_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    observed_at: AwareDatetime
    code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    health: Literal["healthy", "unhealthy", "unreachable"]
    service_count: int = Field(ge=1, le=12)
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RelayReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[2]
    evidence_type: Literal["relay"]
    evidence_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    transition_id: str = Field(pattern=r"^[a-zA-Z0-9_.:-]{1,100}$")
    observed_at: AwareDatetime
    state: Literal["connected", "disconnected"]
    owner_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


OperationReceipt = Annotated[
    LaunchdReceipt | StageReceipt | RailwayReceipt | RelayReceipt,
    Field(discriminator="evidence_type"),
]
_ADAPTER = TypeAdapter(OperationReceipt)


def read_operation_receipts(
    path: Path,
    now: dt.datetime,
) -> tuple[OperationReceipt, ...] | str:
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
            return "system_operations_permissions_invalid"
        payload = path.read_bytes()
        if not payload or len(payload) > 128 * 1024:
            return "system_operations_invalid"
        if _FORBIDDEN.search(payload.decode("utf-8", errors="ignore")) is not None:
            return "system_operations_forbidden_content"
        receipts = tuple(_ADAPTER.validate_json(line) for line in payload.splitlines())
    except (OSError, ValidationError, ValueError):
        return "system_operations_invalid"
    if len({item.evidence_id for item in receipts}) != len(receipts):
        return "system_operations_conflict"
    if any(item.observed_at > now + dt.timedelta(minutes=5) for item in receipts):
        return "system_operations_future"
    evidence_types = {item.evidence_type for item in receipts}
    if "launchd" not in evidence_types:
        return "launchd_receipt_missing"
    if "railway" not in evidence_types:
        return "railway_receipt_missing"
    if "relay" not in evidence_types:
        return "relay_receipt_missing"
    relay_receipts = tuple(
        item for item in receipts if isinstance(item, RelayReceipt)
    )
    if tuple(item.observed_at for item in relay_receipts) != tuple(
        sorted(item.observed_at for item in relay_receipts)
    ):
        return "relay_event_order_invalid"
    return receipts

__all__ = (
    "OPERATIONS_FILE",
    "LaunchdReceipt",
    "OperationReceipt",
    "RailwayReceipt",
    "RelayReceipt",
    "StageReceipt",
    "read_operation_receipts",
)
