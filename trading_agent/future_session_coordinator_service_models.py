from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.future_session_coordinator_models import (
    FutureSessionCoordinatorReceipt,
)

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
MIN_COORDINATOR_POLL_SECONDS = 1
MAX_COORDINATOR_POLL_SECONDS = 3600


class InvalidFutureSessionCoordinatorServiceModelError(ValueError):
    pass


class FutureSessionServiceResult(StrEnum):
    ACTIVATED = "activated"
    WAITING_AUTHORITY = "waiting_authority"
    BLOCKED = "blocked"


class FutureSessionCoordinatorServiceState(StrEnum):
    READY = "ready"
    FAILED = "failed"


class FutureSessionCoordinatorServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[3] = 3
    us_template_request_path: Path
    kr_template_request_path: Path
    us_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kr_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    state_root: Path
    launch_agents_dir: Path
    authority_repository: Path
    scheduler_main_sha: str
    poll_interval_seconds: int = Field(
        ge=MIN_COORDINATOR_POLL_SECONDS,
        le=MAX_COORDINATOR_POLL_SECONDS,
    )

    @model_validator(mode="after")
    def validate_config(self) -> Self:
        paths = (
            self.us_template_request_path,
            self.kr_template_request_path,
            self.state_root,
            self.launch_agents_dir,
            self.authority_repository,
        )
        if any(not path.is_absolute() for path in paths) or _GIT_SHA.fullmatch(self.scheduler_main_sha) is None:
            raise InvalidFutureSessionCoordinatorServiceModelError
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
            raise InvalidFutureSessionCoordinatorServiceModelError
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
            raise InvalidFutureSessionCoordinatorServiceModelError
        return self


class FutureSessionCoordinatorServiceReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[3] = 3
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    us_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kr_template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    service_started_at: AwareDatetime
    observed_at: AwareDatetime
    service_state: FutureSessionCoordinatorServiceState
    scheduler_main_sha: str | None
    frozen_runtime: Path | None
    us: FutureSessionMarketStatus
    kr: FutureSessionMarketStatus

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.observed_at < self.service_started_at or (
            self.service_state is FutureSessionCoordinatorServiceState.READY
        ) != (self.scheduler_main_sha is not None and self.frozen_runtime is not None):
            raise InvalidFutureSessionCoordinatorServiceModelError
        return self


def canonical_service_config_json(value: FutureSessionCoordinatorServiceConfig) -> str:
    return _canonical(value)


def canonical_service_config_sha256(value: FutureSessionCoordinatorServiceConfig) -> str:
    return hashlib.sha256(canonical_service_config_json(value).encode()).hexdigest()


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
    "MAX_COORDINATOR_POLL_SECONDS",
    "MIN_COORDINATOR_POLL_SECONDS",
    "FutureSessionCoordinatorServiceConfig",
    "FutureSessionCoordinatorServiceReport",
    "FutureSessionCoordinatorServiceState",
    "FutureSessionMarketStatus",
    "FutureSessionServiceResult",
    "FutureSessionTickAuthority",
    "InvalidFutureSessionCoordinatorServiceModelError",
    "canonical_service_config_json",
    "canonical_service_config_sha256",
    "canonical_service_report_json",
)
