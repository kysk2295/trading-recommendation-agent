from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import NewType, override

from trading_agent.paper_execution_models import (
    AccountFingerprint,
    PaperBrokerState,
    PaperOrderSnapshot,
    PaperTradeActivity,
)

PaperStreamRecoveryKey = NewType("PaperStreamRecoveryKey", str)


class PaperRecoveryOrderSource(StrEnum):
    OPEN = "open"
    TARGETED = "targeted"
    RECENT = "recent"


class InvalidPaperStreamRecoveryError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "Alpaca paper 스트림 REST 복구 증거가 올바르지 않습니다"


class PaperStreamRecoveryConflictError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "같은 paper 스트림 복구 key의 immutable 필드가 다릅니다"


@dataclass(frozen=True, slots=True)
class PaperRecoveryOrderObservation:
    source: PaperRecoveryOrderSource
    order: PaperOrderSnapshot


@dataclass(frozen=True, slots=True)
class PaperRecoveryState:
    broker_state: PaperBrokerState
    targeted_orders: tuple[PaperOrderSnapshot, ...]
    recent_orders: tuple[PaperOrderSnapshot, ...] = ()
    activities: tuple[PaperTradeActivity, ...] = ()


@dataclass(frozen=True, slots=True)
class PaperStreamRecoveryObservation:
    account_fingerprint: AccountFingerprint
    connection_epoch: str
    started_at: dt.datetime
    completed_at: dt.datetime
    snapshot_json: str
    execution_detail_complete: bool
    orders: tuple[PaperRecoveryOrderObservation, ...] = ()
    activities: tuple[PaperTradeActivity, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredPaperStreamRecovery:
    recovery_id: int
    recovery_key: PaperStreamRecoveryKey
    account_fingerprint: AccountFingerprint
    connection_epoch: str
    started_at: str
    completed_at: str
    snapshot_json: str
    snapshot_sha256: str
    orders_sha256: str
    activities_sha256: str
    execution_detail_complete: bool


@dataclass(frozen=True, slots=True)
class StoredPaperRecoveryOrder:
    recovery_id: int
    recovery_key: PaperStreamRecoveryKey
    source: PaperRecoveryOrderSource
    order: PaperOrderSnapshot
