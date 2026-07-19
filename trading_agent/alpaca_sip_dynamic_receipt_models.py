from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import override

_EPOCH = re.compile(r"^[0-9a-f]{32}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class AlpacaSipDynamicReceiptError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca SIP dynamic receipt is invalid"


class AlpacaSipDynamicReceiptKind(StrEnum):
    CONTROL = "control"
    DATA = "data"


@dataclass(frozen=True, slots=True)
class AlpacaSipDynamicRawReceipt:
    connection_epoch: str
    sequence: int
    received_at: dt.datetime
    kind: AlpacaSipDynamicReceiptKind
    payload: bytes

    def __post_init__(self) -> None:
        if (
            _EPOCH.fullmatch(self.connection_epoch) is None
            or type(self.sequence) is not int
            or self.sequence <= 0
            or not _aware(self.received_at)
            or type(self.kind) is not AlpacaSipDynamicReceiptKind
            or type(self.payload) is not bytes
            or not self.payload
        ):
            raise AlpacaSipDynamicReceiptError


@dataclass(frozen=True, slots=True)
class StoredAlpacaSipDynamicReceipt:
    generation: int
    receipt_id: str
    plan_id: str
    connection_epoch: str
    sequence: int
    received_at: dt.datetime
    kind: AlpacaSipDynamicReceiptKind
    payload_sha256: str
    payload: bytes

    def __post_init__(self) -> None:
        if (
            type(self.generation) is not int
            or self.generation <= 0
            or _HEX64.fullmatch(self.receipt_id) is None
            or _HEX64.fullmatch(self.plan_id) is None
            or _HEX64.fullmatch(self.payload_sha256) is None
        ):
            raise AlpacaSipDynamicReceiptError
        _ = AlpacaSipDynamicRawReceipt(
            self.connection_epoch,
            self.sequence,
            self.received_at,
            self.kind,
            self.payload,
        )


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "AlpacaSipDynamicRawReceipt",
    "AlpacaSipDynamicReceiptError",
    "AlpacaSipDynamicReceiptKind",
    "StoredAlpacaSipDynamicReceipt",
)
