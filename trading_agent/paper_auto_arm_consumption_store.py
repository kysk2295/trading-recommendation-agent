from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.hermes_arm_request import (
    HermesArmFailure,
    HermesArmScope,
    InvalidHermesArmRequestError,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_RECEIPT_BYTES = 4096


class PaperAutoArmConsumptionReceipt(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        hide_input_in_errors=True,
    )

    schema_version: Literal[1] = 1
    request_id: str
    scope: HermesArmScope
    strategy_version: str

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        if _SHA256.fullmatch(self.request_id) is None or not self.strategy_version:
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
        return self


class PaperAutoArmConsumptionStore:
    __slots__ = ("root",)

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
        self.root = Path(os.path.abspath(root))

    @classmethod
    def for_execution_database(cls, database: Path) -> PaperAutoArmConsumptionStore:
        if not database.is_absolute():
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
        resolved = database.resolve(strict=False)
        return cls(resolved.parent / f".{resolved.name}.paper-auto-arm-consumptions")

    def claim(self, receipt: PaperAutoArmConsumptionReceipt) -> None:
        checked = PaperAutoArmConsumptionReceipt.model_validate(receipt.model_dump(mode="python"))
        try:
            _require_private_directory(self.root)
            descriptor = os.open(
                self.root / ".writer.lock",
                os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
                0o600,
            )
            _require_private_file_descriptor(descriptor)
            with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                destination = self.root / f"{checked.request_id}.json"
                if os.path.lexists(destination):
                    if _read_receipt(destination) == checked:
                        raise InvalidHermesArmRequestError(HermesArmFailure.CONSUMED)
                    raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
                _write_receipt(destination, checked)
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        except InvalidHermesArmRequestError:
            raise
        except (OSError, TypeError, ValidationError, ValueError):
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE) from None


def canonical_paper_auto_arm_consumption_receipt_json(
    receipt: PaperAutoArmConsumptionReceipt,
) -> str:
    return (
        json.dumps(
            receipt.model_dump(mode="json"),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    )


def _require_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)


def _require_private_descriptor(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)


def _require_private_file_descriptor(descriptor: int) -> None:
    try:
        _require_private_descriptor(descriptor)
    except (InvalidHermesArmRequestError, OSError):
        os.close(descriptor)
        raise


def _write_receipt(
    path: Path,
    receipt: PaperAutoArmConsumptionReceipt,
) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    _require_private_file_descriptor(descriptor)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        _ = handle.write(canonical_paper_auto_arm_consumption_receipt_json(receipt))
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(path.parent)


def _read_receipt(path: Path) -> PaperAutoArmConsumptionReceipt:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    _require_private_file_descriptor(descriptor)
    with os.fdopen(descriptor, "rb") as handle:
        payload = handle.read(_MAX_RECEIPT_BYTES + 1)
    if len(payload) > _MAX_RECEIPT_BYTES:
        raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
    receipt = PaperAutoArmConsumptionReceipt.model_validate_json(payload)
    if payload.decode() != canonical_paper_auto_arm_consumption_receipt_json(receipt):
        raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
    return receipt


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise InvalidHermesArmRequestError(HermesArmFailure.INVALID_STORE)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = (
    "PaperAutoArmConsumptionReceipt",
    "PaperAutoArmConsumptionStore",
    "canonical_paper_auto_arm_consumption_receipt_json",
)
