from __future__ import annotations

import datetime as dt
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.future_session_plan_models import (
    FutureSessionMarket,
    WaitingAuthorityReason,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FutureSessionCoordinatorResult(StrEnum):
    ACTIVATED = "activated"
    BLOCKED = "blocked"
    WAITING_AUTHORITY = "waiting_authority"


class FutureSessionPreparationResult(StrEnum):
    PREPARED = "prepared"
    ALREADY_PREPARED = "already_prepared"
    NOT_PREPARED = "not_prepared"


class FutureSessionActivationResult(StrEnum):
    ACTIVATED = "activated"
    ALREADY_ACTIVATED = "already_activated"
    NOT_ACTIVATED = "not_activated"


class FutureSessionCoordinatorBlockReason(StrEnum):
    INVALID_REQUEST = "invalid_request"
    PLAN_CONFLICT = "plan_conflict"
    PREPARATION_CONFLICT = "preparation_conflict"
    ACTIVATION_RECEIPT_MISMATCH = "activation_receipt_mismatch"
    PARTIAL_INSTALLED_SET = "partial_installed_set"
    INSTALLED_PLIST_MISMATCH = "installed_plist_mismatch"
    LAUNCHCTL_LABEL_MISMATCH = "launchctl_label_mismatch"
    DESTINATION_CLAIMED = "destination_claimed"
    MATERIALIZATION_FAILED = "materialization_failed"
    ACTIVATION_FAILED = "activation_failed"
    ARTIFACT_IO_FAILED = "artifact_io_failed"
    CONCURRENT_COORDINATOR = "concurrent_coordinator"


class FutureSessionCoordinatorRequest(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
    )

    request_path: Path
    plan_path: Path
    launch_agents_dir: Path

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        if not all(
            path.is_absolute()
            for path in (
                self.request_path,
                self.plan_path,
                self.launch_agents_dir,
            )
        ):
            raise ValueError("coordinator paths must be absolute")
        return self


class UsFutureSessionActivationReceipt(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
    )

    schema_version: Literal[2] = 2
    labels: tuple[str, ...]
    manifest_sha256: str
    result: Literal["activated"] = "activated"

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if (
            not self.labels
            or len(set(self.labels)) != len(self.labels)
            or any(not label for label in self.labels)
            or _SHA256.fullmatch(self.manifest_sha256) is None
        ):
            raise ValueError("invalid US activation receipt")
        return self


class KrFutureSessionActivationReceipt(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
    )

    schema_version: Literal[1] = 1
    label: str
    manifest_sha256: str
    result: Literal["activated"] = "activated"

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if not self.label or _SHA256.fullmatch(self.manifest_sha256) is None:
            raise ValueError("invalid KR activation receipt")
        return self


class FutureSessionCoordinatorReceipt(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
    )

    schema_version: Literal[1] = 1
    result: FutureSessionCoordinatorResult
    market: FutureSessionMarket
    target_session: dt.date | None
    preparation: FutureSessionPreparationResult
    activation: FutureSessionActivationResult
    plan_path: Path | None = None
    manifest_path: Path | None = None
    activation_receipt: Path | None = None
    waiting_reasons: tuple[WaitingAuthorityReason, ...] = ()
    reason: FutureSessionCoordinatorBlockReason | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        match self.result:
            case FutureSessionCoordinatorResult.ACTIVATED:
                valid = (
                    self.target_session is not None
                    and self.preparation
                    in (
                        FutureSessionPreparationResult.PREPARED,
                        FutureSessionPreparationResult.ALREADY_PREPARED,
                    )
                    and self.activation
                    in (
                        FutureSessionActivationResult.ACTIVATED,
                        FutureSessionActivationResult.ALREADY_ACTIVATED,
                    )
                    and self.plan_path is not None
                    and self.manifest_path is not None
                    and self.activation_receipt is not None
                    and not self.waiting_reasons
                    and self.reason is None
                )
            case FutureSessionCoordinatorResult.WAITING_AUTHORITY:
                valid = (
                    self.preparation is FutureSessionPreparationResult.NOT_PREPARED
                    and self.activation is FutureSessionActivationResult.NOT_ACTIVATED
                    and self.plan_path is None
                    and self.manifest_path is None
                    and self.activation_receipt is None
                    and bool(self.waiting_reasons)
                    and self.reason is None
                )
            case FutureSessionCoordinatorResult.BLOCKED:
                valid = (
                    self.activation is FutureSessionActivationResult.NOT_ACTIVATED
                    and not self.waiting_reasons
                    and self.reason is not None
                )
        if not valid:
            raise ValueError("invalid coordinator receipt")
        return self


def canonical_coordinator_receipt_json(
    value: FutureSessionCoordinatorReceipt,
) -> str:
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
    "FutureSessionActivationResult",
    "FutureSessionCoordinatorBlockReason",
    "FutureSessionCoordinatorReceipt",
    "FutureSessionCoordinatorRequest",
    "FutureSessionCoordinatorResult",
    "FutureSessionPreparationResult",
    "KrFutureSessionActivationReceipt",
    "UsFutureSessionActivationReceipt",
    "canonical_coordinator_receipt_json",
)
