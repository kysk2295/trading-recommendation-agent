from __future__ import annotations

import datetime as dt
import json
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.future_session_coordinator_models import (
    FutureSessionCoordinatorReceipt,
)


class FutureSessionServiceResult(StrEnum):
    ACTIVATED = "activated"
    WAITING_AUTHORITY = "waiting_authority"
    BLOCKED = "blocked"


class FutureSessionCoordinatorServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    us_template_request_path: Path
    kr_template_request_path: Path
    state_root: Path
    launch_agents_dir: Path
    authority_repository: Path
    poll_interval_seconds: int

    @model_validator(mode="after")
    def validate_config(self) -> Self:
        paths = (
            self.us_template_request_path,
            self.kr_template_request_path,
            self.state_root,
            self.launch_agents_dir,
            self.authority_repository,
        )
        if any(not path.is_absolute() for path in paths) or self.poll_interval_seconds <= 0:
            raise ValueError("invalid future-session coordinator service config")
        return self


class FutureSessionTickAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    observed_at: dt.datetime
    scheduler_main_sha: str
    frozen_runtime: Path

    @model_validator(mode="after")
    def validate_authority(self) -> Self:
        if (
            self.observed_at.tzinfo is None
            or self.observed_at.utcoffset() is None
            or not self.frozen_runtime.is_absolute()
        ):
            raise ValueError("invalid tick authority")
        return self


class FutureSessionMarketStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    result: FutureSessionServiceResult
    request_path: Path | None = None
    plan_path: Path | None = None
    receipt: FutureSessionCoordinatorReceipt | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if (self.receipt is None) == (self.reason is None):
            raise ValueError("market status requires exactly one outcome")
        return self


class FutureSessionCoordinatorServiceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    observed_at: dt.datetime
    scheduler_main_sha: str | None
    frozen_runtime: Path | None
    us: FutureSessionMarketStatus
    kr: FutureSessionMarketStatus

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("report timestamp must be timezone aware")
        return self


def canonical_service_config_json(value: FutureSessionCoordinatorServiceConfig) -> str:
    return _canonical(value)


def canonical_service_report_json(value: FutureSessionCoordinatorServiceReport) -> str:
    return _canonical(value)


def _canonical(value: BaseModel) -> str:
    return (
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


__all__ = (
    "FutureSessionCoordinatorServiceConfig",
    "FutureSessionCoordinatorServiceReport",
    "FutureSessionMarketStatus",
    "FutureSessionServiceResult",
    "FutureSessionTickAuthority",
    "canonical_service_config_json",
    "canonical_service_report_json",
)
