from __future__ import annotations

import csv
import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, assert_never

import httpx2
from pydantic import BaseModel, ConfigDict, NonNegativeInt, PositiveFloat

from scr_backtest.kis_http import get_with_server_retry
from scr_backtest.kis_intraday import KisApiError, KisSession
from trading_agent.kis_auth import quote_headers
from trading_agent.kis_live import regular_session_is_open
from trading_agent.kis_provider import KisRankedStock
from trading_agent.market_risk import MarketRiskScreen
from trading_agent.opening_gap_checkpoint import repair_transient_cycle_rows
from trading_agent.us_equity_calendar import NEW_YORK

PRICE_DETAIL_PATH: Final = "/uapi/overseas-price/v1/quotations/price-detail"
PRICE_DETAIL_TRANSACTION_ID: Final = "HHDFS76200200"
REQUEST_DELAY_SECONDS: Final = 0.08
SNAPSHOT_HEADER: Final = (
    "ranking_observed_at",
    "quote_observed_at",
    "exchange",
    "symbol",
    "status",
    "previous_close",
    "session_open",
    "opening_gap_pct",
    "current_price",
    "current_volume",
    "previous_volume",
    "error",
)
CYCLE_HEADER: Final = (
    "ranking_observed_at",
    "status",
    "eligible_count",
    "success_count",
    "failure_count",
)
REUSE_CYCLE_HEADER: Final = (
    "ranking_observed_at",
    "status",
    "eligible_count",
    "reused_success_count",
    "attempted_count",
    "new_success_count",
    "failure_count",
)


class KisPriceDetailOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    base: PositiveFloat
    open: PositiveFloat
    last: PositiveFloat
    tvol: NonNegativeInt
    pvol: NonNegativeInt


class KisPriceDetailPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    rt_cd: str
    msg_cd: str
    msg1: str
    output: KisPriceDetailOutput | None = None


class OpeningGapCycleStatus(StrEnum):
    MARKET_CLOSED = "market_closed"
    NO_ELIGIBLE_CANDIDATES = "no_eligible_candidates"
    COLLECTED = "collected"


@dataclass(frozen=True, slots=True)
class OpeningGapQuote:
    observed_at: dt.datetime
    stock: KisRankedStock
    previous_close: float
    session_open: float
    opening_gap_pct: float
    current_price: float
    current_volume: int
    previous_volume: int


@dataclass(frozen=True, slots=True)
class OpeningGapFailure:
    observed_at: dt.datetime
    stock: KisRankedStock
    error: str


OpeningGapResult = OpeningGapQuote | OpeningGapFailure


@dataclass(frozen=True, slots=True)
class OpeningGapCycle:
    ranking_observed_at: dt.datetime
    status: OpeningGapCycleStatus
    eligible_count: int
    results: tuple[OpeningGapResult, ...]
    reused_success_count: int = 0

    @property
    def attempted_count(self) -> int:
        return len(self.results)

    @property
    def new_success_count(self) -> int:
        return sum(isinstance(row, OpeningGapQuote) for row in self.results)

    @property
    def success_count(self) -> int:
        return self.reused_success_count + self.new_success_count

    @property
    def failure_count(self) -> int:
        return sum(isinstance(row, OpeningGapFailure) for row in self.results)


@dataclass(frozen=True, slots=True)
class OpeningGapCapture:
    output_dir: Path
    session: KisSession
    ranking_observed_at: dt.datetime
    screen: MarketRiskScreen


@dataclass(frozen=True, slots=True)
class OpeningGapRuntime:
    clock: Callable[[], dt.datetime]
    sleeper: Callable[[float], None]


DEFAULT_RUNTIME: Final = OpeningGapRuntime(
    lambda: dt.datetime.now(dt.UTC),
    time.sleep,
)


def capture_opening_gaps(
    client: httpx2.Client,
    capture: OpeningGapCapture,
    runtime: OpeningGapRuntime = DEFAULT_RUNTIME,
) -> OpeningGapCycle:
    _ = repair_transient_cycle_rows(capture.output_dir)
    candidates = (*capture.screen.selected, *capture.screen.not_selected)
    snapshot_path = capture.output_dir / "kis_opening_gap_snapshots.csv"
    if not regular_session_is_open(capture.ranking_observed_at):
        cycle = OpeningGapCycle(
            capture.ranking_observed_at,
            OpeningGapCycleStatus.MARKET_CLOSED,
            len(candidates),
            (),
        )
    elif not candidates:
        cycle = OpeningGapCycle(
            capture.ranking_observed_at,
            OpeningGapCycleStatus.NO_ELIGIBLE_CANDIDATES,
            0,
            (),
        )
    else:
        captured = _successful_snapshot_keys(
            snapshot_path,
            capture.ranking_observed_at,
        )
        pending = tuple(stock for stock in candidates if (stock.exchange, stock.symbol) not in captured)
        results: list[OpeningGapResult] = []
        for index, stock in enumerate(pending):
            try:
                detail = _fetch_price_detail(client, capture.session, stock)
                observed_at = runtime.clock()
                results.append(
                    OpeningGapQuote(
                        observed_at,
                        stock,
                        detail.base,
                        detail.open,
                        detail.open / detail.base - 1.0,
                        detail.last,
                        detail.tvol,
                        detail.pvol,
                    )
                )
            except (httpx2.HTTPError, KisApiError) as error:
                results.append(
                    OpeningGapFailure(
                        runtime.clock(),
                        stock,
                        str(error).replace("\n", " "),
                    )
                )
            if index + 1 < len(pending):
                runtime.sleeper(REQUEST_DELAY_SECONDS)
        cycle = OpeningGapCycle(
            capture.ranking_observed_at,
            OpeningGapCycleStatus.COLLECTED,
            len(candidates),
            tuple(results),
            len(candidates) - len(pending),
        )
    _append_cycle(capture.output_dir / "kis_opening_gap_cycles.csv", cycle)
    _append_reuse_cycle(
        capture.output_dir / "kis_opening_gap_reuse_cycles.csv",
        cycle,
    )
    if cycle.results:
        _append_snapshots(snapshot_path, cycle)
    return cycle


def _successful_snapshot_keys(
    path: Path,
    observed_at: dt.datetime,
) -> frozenset[tuple[str, str]]:
    if not path.is_file():
        return frozenset()
    session_date = observed_at.astimezone(NEW_YORK).date()
    keys: set[tuple[str, str]] = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") != "ok":
                continue
            try:
                ranking_at = dt.datetime.fromisoformat(row.get("ranking_observed_at", ""))
            except ValueError:
                continue
            if ranking_at.tzinfo is None or ranking_at.astimezone(NEW_YORK).date() != session_date:
                continue
            exchange = row.get("exchange", "")
            symbol = row.get("symbol", "")
            if exchange and symbol:
                keys.add((exchange, symbol))
    return frozenset(keys)


def _fetch_price_detail(
    client: httpx2.Client,
    session: KisSession,
    stock: KisRankedStock,
) -> KisPriceDetailOutput:
    response = get_with_server_retry(
        client,
        PRICE_DETAIL_PATH,
        params={"AUTH": "", "EXCD": stock.exchange, "SYMB": stock.symbol},
        headers=quote_headers(
            session.credentials,
            session.access_token,
            PRICE_DETAIL_TRANSACTION_ID,
        ),
    )
    _ = response.raise_for_status()
    payload = KisPriceDetailPayload.model_validate_json(response.text)
    if payload.rt_cd != "0":
        raise KisApiError(code=payload.msg_cd, message=payload.msg1)
    if payload.output is None:
        raise KisApiError(
            code="KIS_PRICE_DETAIL_MISSING",
            message="현재가상세 output이 없습니다",
        )
    return payload.output


def _append_cycle(path: Path, cycle: OpeningGapCycle) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    has_header = path.is_file() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not has_header:
            writer.writerow(CYCLE_HEADER)
        writer.writerow(
            (
                cycle.ranking_observed_at.isoformat(),
                cycle.status.value,
                cycle.eligible_count,
                cycle.success_count,
                cycle.failure_count,
            )
        )


def _append_reuse_cycle(path: Path, cycle: OpeningGapCycle) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    has_header = path.is_file() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not has_header:
            writer.writerow(REUSE_CYCLE_HEADER)
        writer.writerow(
            (
                cycle.ranking_observed_at.isoformat(),
                cycle.status.value,
                cycle.eligible_count,
                cycle.reused_success_count,
                cycle.attempted_count,
                cycle.new_success_count,
                cycle.failure_count,
            )
        )


def _append_snapshots(path: Path, cycle: OpeningGapCycle) -> None:
    has_header = path.is_file() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not has_header:
            writer.writerow(SNAPSHOT_HEADER)
        for result in cycle.results:
            match result:
                case OpeningGapQuote():
                    writer.writerow(
                        (
                            cycle.ranking_observed_at.isoformat(),
                            result.observed_at.isoformat(),
                            result.stock.exchange,
                            result.stock.symbol,
                            "ok",
                            result.previous_close,
                            result.session_open,
                            f"{result.opening_gap_pct:.12f}",
                            result.current_price,
                            result.current_volume,
                            result.previous_volume,
                            "",
                        )
                    )
                case OpeningGapFailure():
                    writer.writerow(
                        (
                            cycle.ranking_observed_at.isoformat(),
                            result.observed_at.isoformat(),
                            result.stock.exchange,
                            result.stock.symbol,
                            "error",
                            "",
                            "",
                            "",
                            "",
                            "",
                            "",
                            result.error,
                        )
                    )
                case unreachable:
                    assert_never(unreachable)
