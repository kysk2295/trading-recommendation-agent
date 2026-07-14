from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

CSV_HEADER: Final = (
    "symbol",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "vwap",
)


@dataclass(frozen=True, slots=True)
class AlpacaBarWindow:
    start: dt.time
    end: dt.time

    def __post_init__(self) -> None:
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError("Alpaca 분봉 구간은 뉴욕 현지시각이어야 합니다")
        if self.start >= self.end:
            raise ValueError("Alpaca 분봉 종료시각은 시작시각보다 늦어야 합니다")


FULL_SESSION_WINDOW: Final = AlpacaBarWindow(start=dt.time(4), end=dt.time(20))


@dataclass(frozen=True, slots=True)
class AlpacaArchiveResult:
    session_date: dt.date
    archive_dir: Path
    batch_count: int
    skipped_batch_count: int
    bar_count: int
    request_count: int
    new_request_count: int


class AlpacaBar(BaseModel):
    model_config = ConfigDict(frozen=True)

    timestamp: dt.datetime = Field(alias="t")
    open: float = Field(alias="o")
    high: float = Field(alias="h")
    low: float = Field(alias="l")
    close: float = Field(alias="c")
    volume: int = Field(alias="v")
    trade_count: int = Field(alias="n")
    vwap: float | None = Field(default=None, alias="vw")


class AlpacaBarsPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    bars: dict[str, tuple[AlpacaBar, ...]]
    next_page_token: str | None = None


class AlpacaErrorPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    message: str


class BatchCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str
    session_date: dt.date
    bar_count: int
    request_count: int
    symbols: tuple[str, ...]
    feed: str
    window_start: dt.time = FULL_SESSION_WINDOW.start
    window_end: dt.time = FULL_SESSION_WINDOW.end


ERROR_ADAPTER: Final = TypeAdapter(AlpacaErrorPayload)
CHECKPOINT_ADAPTER: Final = TypeAdapter(BatchCheckpoint)
BARS_ADAPTER: Final = TypeAdapter(AlpacaBarsPayload)
