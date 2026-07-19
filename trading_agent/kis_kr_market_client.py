from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final, Self, final, override
from zoneinfo import ZoneInfo

import httpx2
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_auth import quote_headers
from trading_agent.kis_kr_market_models import (
    KisKrMarketReceipt,
    KisKrMarketReceiptKind,
)
from trading_agent.kr_instrument import is_kr_instrument_symbol_v2

KIS_KR_MARKET_BASE_URL: Final = "https://openapi.koreainvestment.com:9443"
SEOUL: Final = ZoneInfo("Asia/Seoul")
_ONE_MINUTE: Final = dt.timedelta(minutes=1)
_MAX_REQUEST_SKEW: Final = dt.timedelta(seconds=2)
_SESSION_OPEN: Final = dt.time(9)
_LAST_MINUTE_START: Final = dt.time(15, 29)


class UnsafeKisKrMarketEndpointError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR market client endpoint must be the official live origin"


class UnsafeKisKrMarketRedirectPolicyError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR market client must not follow redirects"


class KisKrMarketTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KIS KR market read-only transport failed"


class KisKrMarketFetchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: KisKrMarketReceiptKind
    symbol: str
    requested_at: dt.datetime
    minute_end_at: dt.datetime | None = None

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        minute_valid = (
            self.minute_end_at is not None
            and _aware(self.minute_end_at)
            and self.minute_end_at.second == 0
            and self.minute_end_at.microsecond == 0
            and self.minute_end_at.astimezone(SEOUL).date() == self.requested_at.astimezone(SEOUL).date()
            and _SESSION_OPEN <= self.minute_end_at.astimezone(SEOUL).time() <= _LAST_MINUTE_START
            and self.minute_end_at + _ONE_MINUTE <= self.requested_at
            if self.kind is KisKrMarketReceiptKind.MINUTE_BARS
            else self.minute_end_at is None
        )
        if not is_kr_instrument_symbol_v2(self.symbol) or not _aware(self.requested_at) or not minute_valid:
            raise KisKrMarketTransportError
        return self


@dataclass(frozen=True, slots=True)
class _Contract:
    path: str
    tr_id: str


_CONTRACTS: Final[Mapping[KisKrMarketReceiptKind, _Contract]] = {
    KisKrMarketReceiptKind.MINUTE_BARS: _Contract(
        "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
        "FHKST03010200",
    ),
    KisKrMarketReceiptKind.PRICE_STATUS: _Contract(
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        "FHKST01010100",
    ),
    KisKrMarketReceiptKind.ORDER_BOOK: _Contract(
        "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
        "FHKST01010200",
    ),
}


@final
class KisKrMarketClient:
    def __init__(
        self,
        client: httpx2.Client,
        credentials: KisCredentials,
        access_token: str,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if str(client.base_url).rstrip("/") != KIS_KR_MARKET_BASE_URL:
            raise UnsafeKisKrMarketEndpointError
        if client.follow_redirects:
            raise UnsafeKisKrMarketRedirectPolicyError
        if type(access_token) is not str or not access_token:
            raise KisKrMarketTransportError
        self._client = client
        self._credentials = credentials
        self._access_token = access_token
        self._clock = _clock

    def fetch(self, source: KisKrMarketFetchRequest) -> KisKrMarketReceipt:
        request = _validated_request(source)
        contract = _CONTRACTS[request.kind]
        started_at = self._clock()
        if not _aware(started_at) or abs(started_at - request.requested_at) > _MAX_REQUEST_SKEW:
            raise KisKrMarketTransportError
        try:
            response = self._client.get(
                contract.path,
                params=_params(request),
                headers=quote_headers(self._credentials, self._access_token, contract.tr_id),
            )
            received_at = self._clock()
        except httpx2.HTTPError:
            raise KisKrMarketTransportError from None
        payload = bytes(response.content)
        if (
            not _aware(received_at)
            or received_at < started_at
            or not payload
            or _content_type(response) != "application/json"
        ):
            raise KisKrMarketTransportError
        return KisKrMarketReceipt(
            kind=request.kind,
            symbol=request.symbol,
            received_at=received_at,
            status_code=response.status_code,
            content_type="application/json",
            raw_payload=payload,
        )


def _validated_request(source: KisKrMarketFetchRequest) -> KisKrMarketFetchRequest:
    try:
        return KisKrMarketFetchRequest.model_validate(source.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise KisKrMarketTransportError from None


def _params(request: KisKrMarketFetchRequest) -> Mapping[str, str]:
    common = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": request.symbol,
    }
    if request.kind is not KisKrMarketReceiptKind.MINUTE_BARS:
        return common
    if request.minute_end_at is None:
        raise KisKrMarketTransportError
    return {
        **common,
        "FID_INPUT_HOUR_1": request.minute_end_at.astimezone(SEOUL).strftime("%H%M%S"),
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_ETC_CLS_CODE": "",
    }


def _content_type(response: httpx2.Response) -> str:
    return response.headers.get("content-type", "").partition(";")[0].strip().lower()


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
