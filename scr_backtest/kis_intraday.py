from __future__ import annotations

import csv
import datetime as dt
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, NotRequired, TypedDict, override
from zoneinfo import ZoneInfo

import httpx2
from pydantic import TypeAdapter

from scr_backtest.kis_http import get_with_server_retry


class KisMinuteRow(TypedDict):
    xymd: str
    xhms: str
    kymd: str
    khms: str
    open: str
    high: str
    low: str
    last: str
    evol: str
    eamt: str


class KisMinutePayload(TypedDict):
    rt_cd: str
    msg_cd: str
    msg1: str
    output2: NotRequired[list[KisMinuteRow]]


class KisTokenPayload(TypedDict):
    access_token: NotRequired[str]
    error_code: NotRequired[str]
    error_description: NotRequired[str]


MINUTE_PAYLOAD_ADAPTER: Final = TypeAdapter(KisMinutePayload)
TOKEN_PAYLOAD_ADAPTER: Final = TypeAdapter(KisTokenPayload)
NEW_YORK: Final = ZoneInfo("America/New_York")
SEOUL: Final = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True, slots=True, repr=False)
class KisCredentials:
    app_key: str
    app_secret: str

    @override
    def __repr__(self) -> str:
        return "KisCredentials(<redacted>)"


@dataclass(frozen=True, slots=True)
class KisMinuteBar:
    exchange_timestamp: dt.datetime
    korea_timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: int


@dataclass(frozen=True, slots=True)
class KisMinuteRequest:
    exchange: str
    symbol: str
    interval_minutes: int = 1
    include_previous_day: bool = True
    cursor: str = ""
    record_count: int = 120


@dataclass(frozen=True, slots=True, repr=False)
class KisSession:
    credentials: KisCredentials
    access_token: str

    @override
    def __repr__(self) -> str:
        return "KisSession(<redacted>)"


@dataclass(frozen=True, slots=True)
class KisProbeRequest:
    minute: KisMinuteRequest
    page_count: int = 1


@dataclass(frozen=True, slots=True)
class MissingKisCredentialsError(RuntimeError):
    missing_names: tuple[str, ...]

    @override
    def __str__(self) -> str:
        return f"환경변수가 필요합니다: {', '.join(self.missing_names)}"


@dataclass(frozen=True, slots=True)
class KisApiError(RuntimeError):
    code: str
    message: str

    @override
    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def require_kis_credentials(
    environment: Mapping[str, str] | None = None,
) -> KisCredentials:
    source = os.environ if environment is None else environment
    app_key = source.get("KIS_APP_KEY", "").strip()
    app_secret = source.get("KIS_APP_SECRET", "").strip()
    missing_names = tuple(
        name
        for name, value in (
            ("KIS_APP_KEY", app_key),
            ("KIS_APP_SECRET", app_secret),
        )
        if value == ""
    )
    if missing_names:
        raise MissingKisCredentialsError(missing_names=missing_names)
    return KisCredentials(app_key=app_key, app_secret=app_secret)


def parse_minute_payload(payload: KisMinutePayload) -> tuple[KisMinuteBar, ...]:
    if payload["rt_cd"] != "0":
        raise KisApiError(code=payload["msg_cd"], message=payload["msg1"])
    return tuple(
        sorted(
            (
                KisMinuteBar(
                    exchange_timestamp=_parse_timestamp(row["xymd"], row["xhms"], NEW_YORK),
                    korea_timestamp=_parse_timestamp(row["kymd"], row["khms"], SEOUL),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["last"]),
                    volume=int(row["evol"]),
                    amount=int(row["eamt"]),
                )
                for row in payload.get("output2", [])
            ),
            key=lambda bar: bar.exchange_timestamp,
        )
    )


def next_minute_cursor(bars: tuple[KisMinuteBar, ...], interval_minutes: int) -> str:
    earliest = min(bar.exchange_timestamp for bar in bars)
    previous = earliest - dt.timedelta(minutes=interval_minutes)
    return previous.strftime("%Y%m%d%H%M%S")


def _parse_timestamp(date: str, time: str, timezone: ZoneInfo) -> dt.datetime:
    parsed = dt.datetime.strptime(f"{date}{time}", "%Y%m%d%H%M%S")
    return parsed.replace(tzinfo=timezone)


def issue_access_token(client: httpx2.Client, credentials: KisCredentials) -> str:
    response = client.post(
        "/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey": credentials.app_key,
            "appsecret": credentials.app_secret,
        },
    )
    _ = response.raise_for_status()
    payload = TOKEN_PAYLOAD_ADAPTER.validate_json(response.text)
    token = payload.get("access_token", "")
    if token == "":
        raise KisApiError(
            code=payload.get("error_code", "KIS_TOKEN_ERROR"),
            message=payload.get("error_description", "접근토큰 발급 실패"),
        )
    return token


def resolve_access_token(
    client: httpx2.Client,
    credentials: KisCredentials,
    environment: Mapping[str, str] | None = None,
) -> str:
    source = os.environ if environment is None else environment
    token = source.get("KIS_ACCESS_TOKEN", "").strip()
    return token if token else issue_access_token(client, credentials)


def fetch_minute_page(
    client: httpx2.Client,
    credentials: KisCredentials,
    access_token: str,
    request: KisMinuteRequest,
) -> tuple[KisMinuteBar, ...]:
    response = get_with_server_retry(
        client,
        "/uapi/overseas-price/v1/quotations/inquire-time-itemchartprice",
        params={
            "AUTH": "",
            "EXCD": request.exchange,
            "SYMB": request.symbol,
            "NMIN": str(request.interval_minutes),
            "PINC": "1" if request.include_previous_day else "0",
            "NEXT": "1" if request.cursor else "",
            "NREC": str(request.record_count),
            "FILL": "",
            "KEYB": request.cursor,
        },
        headers={
            "authorization": f"Bearer {access_token}",
            "appkey": credentials.app_key,
            "appsecret": credentials.app_secret,
            "tr_id": "HHDFS76950200",
            "custtype": "P",
        },
    )
    _ = response.raise_for_status()
    payload = MINUTE_PAYLOAD_ADAPTER.validate_json(response.text)
    return parse_minute_payload(payload)


def write_minute_csv(path: Path, bars: tuple[KisMinuteBar, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "exchange_timestamp",
                "korea_timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
            )
        )
        writer.writerows(
            (
                bar.exchange_timestamp.isoformat(),
                bar.korea_timestamp.isoformat(),
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.amount,
            )
            for bar in bars
        )


def collect_minute_pages(
    client: httpx2.Client,
    session: KisSession,
    request: KisProbeRequest,
) -> tuple[KisMinuteBar, ...]:
    minute_request = request.minute
    by_timestamp: dict[dt.datetime, KisMinuteBar] = {}
    for _ in range(request.page_count):
        page = fetch_minute_page(
            client,
            session.credentials,
            session.access_token,
            minute_request,
        )
        if not page:
            break
        by_timestamp.update((bar.exchange_timestamp, bar) for bar in page)
        minute_request = replace(
            minute_request,
            cursor=next_minute_cursor(page, minute_request.interval_minutes),
        )
    return tuple(by_timestamp[timestamp] for timestamp in sorted(by_timestamp))
