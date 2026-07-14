from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Final, NotRequired, TypedDict
from zoneinfo import ZoneInfo

import httpx2
from pydantic import TypeAdapter

from scr_backtest.kis_intraday import (
    KisApiError,
    KisCredentials,
    KisMinuteBar,
    KisMinuteRequest,
    fetch_minute_page,
    next_minute_cursor,
)
from trading_agent.kis_auth import quote_headers
from trading_agent.kis_daily import KisDailyContext
from trading_agent.models import BarInput


class KisRankingRow(TypedDict):
    excd: str
    symb: str
    name: str
    last: str
    rate: str
    pask: str
    pbid: str
    tvol: str
    tamt: NotRequired[str]
    a_tvol: NotRequired[str]
    rank: str


class KisRankingPayload(TypedDict):
    rt_cd: str
    msg_cd: str
    msg1: str
    output2: NotRequired[list[KisRankingRow]]


RANKING_ADAPTER: Final = TypeAdapter(KisRankingPayload)
NEW_YORK: Final = ZoneInfo("America/New_York")


@dataclass(frozen=True, slots=True)
class KisRankedStock:
    exchange: str
    symbol: str
    name: str
    price: float
    change_pct: float
    bid: float
    ask: float
    volume: int
    dollar_volume: float
    average_daily_volume: int
    rank: int

    @property
    def prior_close(self) -> float:
        return self.price / (1.0 + self.change_pct)

    @property
    def spread_bps(self) -> float:
        midpoint = (self.bid + self.ask) / 2.0
        if midpoint <= 0.0 or self.ask < self.bid:
            return float("inf")
        return (self.ask - self.bid) / midpoint * 10_000.0


def fetch_volume_ranking(
    client: httpx2.Client,
    credentials: KisCredentials,
    access_token: str,
    exchange: str,
) -> tuple[KisRankedStock, ...]:
    return _fetch_ranking(
        client,
        credentials,
        access_token,
        "/uapi/overseas-stock/v1/ranking/trade-vol",
        {
            "EXCD": exchange,
            "NDAY": "0",
            "VOL_RANG": "3",
            "KEYB": "",
            "AUTH": "",
            "PRC1": "1",
            "PRC2": "200",
        },
        "HHDFS76310010",
    )


def fetch_updown_ranking(
    client: httpx2.Client,
    credentials: KisCredentials,
    access_token: str,
    exchange: str,
) -> tuple[KisRankedStock, ...]:
    return _fetch_ranking(
        client,
        credentials,
        access_token,
        "/uapi/overseas-stock/v1/ranking/updown-rate",
        {
            "EXCD": exchange,
            "NDAY": "0",
            "GUBN": "1",
            "VOL_RANG": "3",
            "AUTH": "",
            "KEYB": "",
        },
        "HHDFS76290000",
    )


def fetch_latest_regular_session(
    client: httpx2.Client,
    credentials: KisCredentials,
    access_token: str,
    stock: KisRankedStock,
    max_pages: int = 10,
) -> tuple[KisMinuteBar, ...]:
    cursor = ""
    collected: dict[dt.datetime, KisMinuteBar] = {}
    for page_number in range(max_pages):
        page = fetch_minute_page(
            client,
            credentials,
            access_token,
            KisMinuteRequest(exchange=stock.exchange, symbol=stock.symbol, cursor=cursor),
        )
        before = len(collected)
        collected.update((bar.exchange_timestamp, bar) for bar in page)
        if not page or len(collected) == before:
            break
        latest_date = max(bar.exchange_timestamp.astimezone(NEW_YORK).date() for bar in collected.values())
        if any(
            bar.exchange_timestamp.astimezone(NEW_YORK).date() == latest_date
            and bar.exchange_timestamp.astimezone(NEW_YORK).time() <= dt.time(9, 30)
            for bar in collected.values()
        ):
            break
        cursor = next_minute_cursor(page, interval_minutes=1)
        if page_number + 1 < max_pages:
            time.sleep(0.08)
    return tuple(sorted(collected.values(), key=lambda bar: bar.exchange_timestamp))


def ranking_to_bar_inputs(
    stock: KisRankedStock,
    bars: tuple[KisMinuteBar, ...],
    daily_context: KisDailyContext | None = None,
) -> tuple[BarInput, ...]:
    if not bars:
        return ()
    latest_date = max(bar.exchange_timestamp.astimezone(NEW_YORK).date() for bar in bars)
    return tuple(
        BarInput(
            symbol=stock.symbol,
            timestamp=bar.exchange_timestamp.astimezone(NEW_YORK),
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            prior_close=(stock.prior_close if daily_context is None else daily_context.prior_close),
            average_daily_volume=(
                max(1, stock.average_daily_volume) if daily_context is None else daily_context.average_daily_volume
            ),
            spread_bps=stock.spread_bps,
            catalyst="KIS 미국주식 거래량 랭킹",
        )
        for bar in bars
        if bar.exchange_timestamp.astimezone(NEW_YORK).date() == latest_date
        and dt.time(9, 30) <= bar.exchange_timestamp.astimezone(NEW_YORK).time() < dt.time(16, 0)
    )


def select_ranked_stocks(
    groups: tuple[tuple[KisRankedStock, ...], ...],
    limit: int,
    min_change_pct: float = 0.04,
) -> tuple[KisRankedStock, ...]:
    unique: dict[tuple[str, str], KisRankedStock] = {}
    for group in groups:
        for stock in group:
            unique[(stock.exchange, stock.symbol)] = stock
    eligible = (
        stock
        for stock in unique.values()
        if stock.change_pct >= min_change_pct and 1.0 <= stock.price <= 200.0 and stock.dollar_volume >= 500_000.0
    )
    return tuple(
        sorted(
            eligible,
            key=lambda stock: (stock.change_pct, stock.dollar_volume),
            reverse=True,
        )[:limit]
    )


def _fetch_ranking(
    client: httpx2.Client,
    credentials: KisCredentials,
    access_token: str,
    path: str,
    params: dict[str, str],
    transaction_id: str,
) -> tuple[KisRankedStock, ...]:
    response = client.get(
        path,
        params=params,
        headers=quote_headers(credentials, access_token, transaction_id),
    )
    _ = response.raise_for_status()
    payload = RANKING_ADAPTER.validate_json(response.text)
    if payload["rt_cd"] != "0":
        raise KisApiError(code=payload["msg_cd"], message=payload["msg1"])
    return tuple(_ranking_row(row) for row in payload.get("output2", []))


def _ranking_row(row: KisRankingRow) -> KisRankedStock:
    price = float(row["last"])
    volume = int(float(row["tvol"]))
    return KisRankedStock(
        exchange=row["excd"],
        symbol=row["symb"],
        name=row["name"],
        price=price,
        change_pct=float(row["rate"]) / 100.0,
        bid=float(row["pbid"]),
        ask=float(row["pask"]),
        volume=volume,
        dollar_volume=float(row.get("tamt", price * volume)),
        average_daily_volume=int(float(row.get("a_tvol", row["tvol"]))),
        rank=int(row["rank"]),
    )
