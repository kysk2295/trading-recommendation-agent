from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.future_session_coordinator_service_models import (
    FutureSessionCoordinatorServiceConfig,
)
from trading_agent.future_session_execution_incident import (
    FutureSessionExecutionIncidentReceipt,
    InvalidFutureSessionExecutionIncidentError,
    canonical_execution_incident_json,
    project_execution_incident,
)
from trading_agent.future_session_execution_incident_authority import (
    validate_execution_incident_authority,
)
from trading_agent.future_session_plan_models import (
    FutureSessionMarket,
)
from trading_agent.future_session_us_activation_verifier import (
    read_private_file,
    verify_private_directory,
)

MAX_PENDING_EXECUTION_INCIDENTS = 128


class FutureSessionExecutionIncidentQueuePointer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    market: FutureSessionMarket
    target_session: dt.date
    role: str
    incident_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_pointer(self) -> Self:
        if not self.role or self.role != self.role.strip() or "--" in self.role:
            raise InvalidFutureSessionExecutionIncidentError
        return self


def canonical_execution_incident_queue_json(pointer: FutureSessionExecutionIncidentQueuePointer) -> str:
    return (
        json.dumps(
            pointer.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def execution_incident_queue_path(
    state_root: Path,
    market: FutureSessionMarket,
    target_session: dt.date,
    role: str,
) -> Path:
    return state_root / "pending-execution-incidents" / f"{market.value}--{target_session.isoformat()}--{role}.json"


def project_pending_execution_incidents(
    config: FutureSessionCoordinatorServiceConfig,
) -> frozenset[tuple[FutureSessionMarket, dt.date]]:
    queue_root = config.state_root / "pending-execution-incidents"
    if not queue_root.exists():
        return frozenset()
    verify_private_directory(queue_root)
    paths = tuple(sorted(queue_root.iterdir()))
    if len(paths) > MAX_PENDING_EXECUTION_INCIDENTS or any(path.suffix != ".json" for path in paths):
        raise InvalidFutureSessionExecutionIncidentError
    projected: set[tuple[FutureSessionMarket, dt.date]] = set()
    for queue_path in paths:
        pointer, pointer_payload = _read_pointer(queue_path)
        artifact_path = _artifact_path(config, pointer)
        receipt, receipt_payload = _read_receipt(artifact_path)
        if hashlib.sha256(receipt_payload).hexdigest() != pointer.incident_sha256:
            raise InvalidFutureSessionExecutionIncidentError
        delivery_database = validate_execution_incident_authority(config, artifact_path, receipt)
        project_execution_incident(receipt, delivery_database)
        _consume_pointer(queue_path, pointer_payload)
        projected.add((receipt.market, receipt.target_session))
    return frozenset(projected)


def _read_pointer(path: Path) -> tuple[FutureSessionExecutionIncidentQueuePointer, bytes]:
    try:
        payload = read_private_file(path, 0o600)
        pointer = FutureSessionExecutionIncidentQueuePointer.model_validate_json(payload)
    except (OSError, TypeError, ValidationError, ValueError):
        raise InvalidFutureSessionExecutionIncidentError from None
    if canonical_execution_incident_queue_json(pointer).encode() != payload or path.name != (
        f"{pointer.market.value}--{pointer.target_session.isoformat()}--{pointer.role}.json"
    ):
        raise InvalidFutureSessionExecutionIncidentError
    return pointer, payload


def _artifact_path(
    config: FutureSessionCoordinatorServiceConfig,
    pointer: FutureSessionExecutionIncidentQueuePointer,
) -> Path:
    return (
        config.state_root
        / "artifacts"
        / pointer.market.value
        / pointer.target_session.isoformat()
        / "execution-incidents"
        / f"{pointer.role}.json"
    )


def _read_receipt(path: Path) -> tuple[FutureSessionExecutionIncidentReceipt, bytes]:
    try:
        payload = read_private_file(path, 0o600)
        receipt = FutureSessionExecutionIncidentReceipt.model_validate_json(payload)
    except (OSError, TypeError, ValidationError, ValueError):
        raise InvalidFutureSessionExecutionIncidentError from None
    if canonical_execution_incident_json(receipt).encode() != payload:
        raise InvalidFutureSessionExecutionIncidentError
    return receipt, payload


def _consume_pointer(path: Path, expected: bytes) -> None:
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory,
        )
        try:
            metadata = os.fstat(descriptor)
            linked = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
            payload = os.read(descriptor, len(expected) + 1)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_nlink != 1
                or (metadata.st_dev, metadata.st_ino) != (linked.st_dev, linked.st_ino)
                or payload != expected
            ):
                raise InvalidFutureSessionExecutionIncidentError
            os.unlink(path.name, dir_fd=directory)
            if os.fstat(descriptor).st_nlink != 0:
                raise InvalidFutureSessionExecutionIncidentError
            os.fsync(directory)
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


__all__ = (
    "MAX_PENDING_EXECUTION_INCIDENTS",
    "FutureSessionExecutionIncidentQueuePointer",
    "canonical_execution_incident_queue_json",
    "execution_incident_queue_path",
    "project_pending_execution_incidents",
)
