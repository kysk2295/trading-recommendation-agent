from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, override

from trading_agent.experiment_ledger_store import ExperimentLedgerStore
from trading_agent.hermes_delivery_store import HermesDeliveryStore
from trading_agent.swing_shadow_review_store import SwingShadowReviewStore
from trading_agent.swing_shadow_store import SwingShadowReader


class SwingOperatingPhase(StrEnum):
    NON_SESSION = "non_session"
    PRE_OPEN = "pre_open"
    REGULAR = "regular"
    POST_CLOSE = "post_close"


class SwingScanFailureReason(StrEnum):
    SOURCE_UNAVAILABLE = "source_unavailable"


@dataclass(frozen=True, slots=True)
class SwingScanCompleted:
    completed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class SwingScanFailed:
    failed_at: dt.datetime
    reason: SwingScanFailureReason


type SwingScanOutcome = SwingScanCompleted | SwingScanFailed


class SwingDailyScanner(Protocol):
    def run(self, session_date: dt.date) -> SwingScanOutcome: ...


class InvalidSwingOperatingRequestError(ValueError):
    @override
    def __str__(self) -> str:
        return "US swing operating tick 요청이 유효하지 않습니다"


@dataclass(frozen=True, slots=True)
class SwingOperatingRequest:
    now: dt.datetime
    runtime_code_version: str


@dataclass(frozen=True, slots=True)
class SwingOperatingConfig:
    experiment_ledger: ExperimentLedgerStore
    shadow_ledger: SwingShadowReader
    delivery_store: HermesDeliveryStore
    review_store: SwingShadowReviewStore
    scanner: SwingDailyScanner


@dataclass(frozen=True, slots=True)
class SwingOperatingResult:
    phase: SwingOperatingPhase
    scanner_executed: bool
    registered: int
    started: int
    finalized: int
    delivered: int
    incidents: int
    reviewed: int
    blocked_signal_ids: tuple[str, ...]
    external_broker_mutations: int = 0


__all__ = (
    "InvalidSwingOperatingRequestError",
    "SwingDailyScanner",
    "SwingOperatingConfig",
    "SwingOperatingPhase",
    "SwingOperatingRequest",
    "SwingOperatingResult",
    "SwingScanCompleted",
    "SwingScanFailed",
    "SwingScanFailureReason",
    "SwingScanOutcome",
)
