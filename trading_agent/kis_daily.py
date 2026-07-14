from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass
from typing import Final, NotRequired, TypedDict

import httpx2
from pydantic import TypeAdapter

from scr_backtest.kis_intraday import KisApiError, KisCredentials
from trading_agent.kis_auth import quote_headers


class KisDailyRow(TypedDict):
    xymd: str
    clos: str
    tvol: str


class KisDailyPayload(TypedDict):
    rt_cd: str
    msg_cd: str
    msg1: str
    output2: NotRequired[list[KisDailyRow]]


DAILY_ADAPTER: Final = TypeAdapter(KisDailyPayload)


@dataclass(frozen=True, slots=True)
class KisDailyContext:
    prior_close: float
    average_daily_volume: int


def fetch_daily_context(
    client: httpx2.Client,
    credentials: KisCredentials,
    access_token: str,
    exchange: str,
    symbol: str,
    session_date: dt.date,
) -> KisDailyContext:
    response = client.get(
        "/uapi/overseas-price/v1/quotations/dailyprice",
        params={
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": symbol,
            "GUBN": "0",
            "BYMD": "",
            "MODP": "1",
        },
        headers=quote_headers(credentials, access_token, "HHDFS76240000"),
    )
    _ = response.raise_for_status()
    payload = DAILY_ADAPTER.validate_json(response.text)
    if payload["rt_cd"] != "0":
        raise KisApiError(code=payload["msg_cd"], message=payload["msg1"])
    completed = sorted(
        (
            (dt.datetime.strptime(row["xymd"], "%Y%m%d").date(), row)
            for row in payload.get("output2", [])
            if dt.datetime.strptime(row["xymd"], "%Y%m%d").date() < session_date
        ),
        reverse=True,
    )[:20]
    if not completed:
        raise KisApiError(code="KIS_DAILY_EMPTY", message=f"{symbol} 완료 일봉 없음")
    return KisDailyContext(
        prior_close=float(completed[0][1]["clos"]),
        average_daily_volume=max(
            1,
            round(statistics.fmean(float(row["tvol"]) for _, row in completed)),
        ),
    )
