from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_SHA: Final = re.compile(r"^[0-9a-f]{40}$")
_REASON_CODE: Final = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
Sha256 = Annotated[str, Field(pattern=_SHA256.pattern)]
CommitSha = Annotated[str, Field(pattern=_COMMIT_SHA.pattern)]
ReasonCode = Annotated[str, Field(pattern=_REASON_CODE.pattern)]


@dataclass(frozen=True, slots=True)
class InvalidSystematicInputActivationModelError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return f"systematic input activation model invalid: {self.reason}"


class BlockedSystematicInputActivation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    status: Literal["blocked"] = "blocked"
    reason_code: ReasonCode
    attempted_at: dt.datetime
    attempt_report_path: Path | None = None
    attempt_report_sha256: Sha256 | None = None

    @field_validator("attempt_report_path")
    @classmethod
    def require_canonical_attempt_path(cls, path: Path | None) -> Path | None:
        if path is not None:
            _require_canonical_path(path)
        return path

    @model_validator(mode="after")
    def require_blocked_contract(self) -> Self:
        _require_aware_time(self.attempted_at)
        if (self.attempt_report_path is None) != (self.attempt_report_sha256 is None):
            raise InvalidSystematicInputActivationModelError("attempt_report_binding_incomplete")
        return self


class ReadySystematicInputActivation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    status: Literal["ready"] = "ready"
    input_csv_path: Path
    input_csv_sha256: Sha256
    dataset_receipt_path: Path
    dataset_receipt_sha256: Sha256
    catalog_receipt_path: Path
    catalog_receipt_sha256: Sha256
    input_binding_receipt_path: Path
    input_binding_receipt_sha256: Sha256
    foundation_path: Path
    foundation_sha256: Sha256
    producer_commit_sha: CommitSha
    input_sha256: Sha256
    selected_session_dates: tuple[dt.date, ...]
    bar_count: int = Field(ge=1, le=100_000)
    max_sessions: int = Field(ge=1, le=60)
    max_bars: int = Field(ge=1, le=100_000)
    rss_limit_gib: float = Field(gt=0, le=10.0)
    activated_at: dt.datetime

    @field_validator(
        "input_csv_path",
        "dataset_receipt_path",
        "catalog_receipt_path",
        "input_binding_receipt_path",
        "foundation_path",
    )
    @classmethod
    def require_canonical_artifact_path(cls, path: Path) -> Path:
        _require_canonical_path(path)
        return path

    @model_validator(mode="after")
    def require_ready_contract(self) -> Self:
        _require_aware_time(self.activated_at)
        if (
            not self.selected_session_dates
            or self.selected_session_dates != tuple(sorted(set(self.selected_session_dates)))
            or len(self.selected_session_dates) > self.max_sessions
            or self.bar_count > self.max_bars
            or self.input_csv_sha256 != self.input_sha256
        ):
            raise InvalidSystematicInputActivationModelError("ready_bounds_invalid")
        return self


SystematicInputActivation = Annotated[
    BlockedSystematicInputActivation | ReadySystematicInputActivation,
    Field(discriminator="status"),
]


def _require_canonical_path(path: Path) -> None:
    canonical = Path(os.path.realpath(path))
    if not path.is_absolute() or path != canonical:
        raise InvalidSystematicInputActivationModelError("artifact_path_not_absolute_canonical")


def _require_aware_time(value: dt.datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidSystematicInputActivationModelError("activation_time_not_aware")


__all__ = (
    "BlockedSystematicInputActivation",
    "InvalidSystematicInputActivationModelError",
    "ReadySystematicInputActivation",
    "SystematicInputActivation",
)
