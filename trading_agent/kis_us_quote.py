from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, Literal, override
from zoneinfo import ZoneInfo

import httpx2
from pydantic import BaseModel, ConfigDict, ValidationError

from scr_backtest.kis_intraday import KisSession
from trading_agent.kis_auth import quote_headers

KIS_US_LEVEL_ONE_PATH: Final = (
    "/uapi/overseas-price/v1/quotations/inquire-asking-price"
)
KIS_US_LEVEL_ONE_TR_ID: Final = "HHDFS76200100"
NEW_YORK: Final = ZoneInfo("America/New_York")
_NON_NEGATIVE_INTEGER: Final = re.compile(r"[0-9]+", flags=re.ASCII)


@dataclass(frozen=True, slots=True)
class KisUsLevelOneQuote:
    exchange: str
    symbol: str
    provider_observed_at: dt.datetime
    received_at: dt.datetime
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int


class KisUsQuoteUnavailableError(RuntimeError):
    __slots__ = ("failure_code",)

    def __init__(self, failure_code: str) -> None:
        super().__init__()
        self.failure_code = failure_code

    @override
    def __str__(self) -> str:
        return (
            "KIS 미국주식 현재 호가를 검증할 수 없습니다: "
            f"{self.failure_code}"
        )


class _StatusEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    rt_cd: str


class _QuoteTimeBlock(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    dymd: str
    dhms: str


class _QuoteBookBlock(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    pbid1: str
    pask1: str
    vbid1: str
    vask1: str


class _SuccessPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    rt_cd: Literal["0"]
    msg_cd: str
    msg1: str
    output1: _QuoteTimeBlock
    output2: _QuoteBookBlock


def fetch_kis_us_level_one_quote(
    client: httpx2.Client,
    session: KisSession,
    *,
    exchange: str,
    symbol: str,
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> KisUsLevelOneQuote:
    response: httpx2.Response | None = None
    request_succeeded = False
    try:
        response = client.get(
            KIS_US_LEVEL_ONE_PATH,
            params={"AUTH": "", "EXCD": exchange, "SYMB": symbol},
            headers=quote_headers(
                session.credentials,
                session.access_token,
                KIS_US_LEVEL_ONE_TR_ID,
            ),
        )
        _ = response.raise_for_status()
        request_succeeded = True
    except httpx2.HTTPError:
        pass
    if not request_succeeded or response is None:
        raise KisUsQuoteUnavailableError("http_error")

    decoded, invalid_json = _decode_json(response.content)
    if invalid_json:
        raise KisUsQuoteUnavailableError("invalid_json")

    status = _validate_status(decoded)
    if status is None:
        raise KisUsQuoteUnavailableError("invalid_response")
    if status.rt_cd != "0":
        raise KisUsQuoteUnavailableError("provider_error")

    payload = _validate_payload(decoded)
    if payload is None:
        raise KisUsQuoteUnavailableError("invalid_response")

    received_at = clock()
    if not _aware(received_at):
        raise KisUsQuoteUnavailableError("invalid_clock")

    provider_observed_at = _provider_timestamp(
        payload.output1.dymd,
        payload.output1.dhms,
    )
    bid = _positive_decimal(payload.output2.pbid1)
    ask = _positive_decimal(payload.output2.pask1)
    bid_size = _non_negative_integer(payload.output2.vbid1)
    ask_size = _non_negative_integer(payload.output2.vask1)
    if bid is None or ask is None or bid_size is None or ask_size is None:
        raise KisUsQuoteUnavailableError("invalid_quote")
    if bid > ask:
        raise KisUsQuoteUnavailableError("invalid_quote")

    return KisUsLevelOneQuote(
        exchange=exchange,
        symbol=symbol,
        provider_observed_at=provider_observed_at,
        received_at=received_at,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
    )


def _decode_json(payload: bytes) -> tuple[object, bool]:
    try:
        return json.loads(payload), False
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, True


def _validate_status(payload: object) -> _StatusEnvelope | None:
    try:
        return _StatusEnvelope.model_validate(payload)
    except ValidationError:
        return None


def _validate_payload(payload: object) -> _SuccessPayload | None:
    try:
        return _SuccessPayload.model_validate(payload)
    except ValidationError:
        return None


def _provider_timestamp(date: str, time: str) -> dt.datetime:
    parsed: dt.datetime | None
    try:
        parsed = dt.datetime.strptime(f"{date}{time}", "%Y%m%d%H%M%S")
    except ValueError:
        parsed = None
    if parsed is None:
        raise KisUsQuoteUnavailableError("invalid_timestamp")
    return parsed.replace(tzinfo=NEW_YORK)


def _positive_decimal(value: str) -> Decimal | None:
    parsed: Decimal | None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        parsed = None
    if parsed is None or not parsed.is_finite() or parsed <= 0:
        return None
    return parsed


def _non_negative_integer(value: str) -> int | None:
    if _NON_NEGATIVE_INTEGER.fullmatch(value) is None:
        return None
    return int(value)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
