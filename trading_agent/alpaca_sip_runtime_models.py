from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, override

from trading_agent.alpaca_models import AlpacaBarsPayload
from trading_agent.intraday_feature_kernel import CompletedMinuteBar

_ERROR_MESSAGE: Final = "alpaca SIP runtime input is invalid"


class AlpacaSipRuntimeError(ValueError):
    def __init__(self) -> None:
        super().__init__(_ERROR_MESSAGE)

    @override
    def __str__(self) -> str:
        return _ERROR_MESSAGE

    @override
    def __repr__(self) -> str:
        return "AlpacaSipRuntimeError()"


@dataclass(frozen=True, slots=True)
class AlpacaSipMinutePageRequest:
    session_date: dt.date
    symbol: str
    start_at: dt.datetime
    end_at: dt.datetime


@dataclass(frozen=True, slots=True)
class AlpacaSipRawPage:
    page_index: int
    page_token: str | None
    received_at: dt.datetime
    raw_response: bytes = field(repr=False)
    payload: AlpacaBarsPayload


@dataclass(frozen=True, slots=True)
class AlpacaSipMinutePage:
    request: AlpacaSipMinutePageRequest
    pages: tuple[AlpacaSipRawPage, ...]


@dataclass(frozen=True, slots=True)
class AlpacaSipRuntimeBar:
    sequence: int
    page_index: int
    canonical_payload: bytes = field(repr=False)
    completed_bar: CompletedMinuteBar


@dataclass(frozen=True, slots=True)
class AlpacaSipRuntimeContext:
    session_date: dt.date
    instrument_id: str
    symbol: str
    clock: Callable[[], dt.datetime] = field(repr=False)


@dataclass(frozen=True, slots=True)
class StoredAlpacaSipRawPage:
    generation: int
    receipt_id: str
    page_index: int
    received_at: dt.datetime
    payload_sha256: str
    raw_response: bytes = field(repr=False)


__all__ = (
    "AlpacaSipMinutePage",
    "AlpacaSipMinutePageRequest",
    "AlpacaSipRawPage",
    "AlpacaSipRuntimeBar",
    "AlpacaSipRuntimeContext",
    "AlpacaSipRuntimeError",
    "StoredAlpacaSipRawPage",
)
