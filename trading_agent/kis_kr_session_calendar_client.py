from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from typing import Final, Self, final, override
from zoneinfo import ZoneInfo

import httpx2
from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_auth import quote_headers
from trading_agent.kis_kr_session_calendar_models import KisKrSessionCalendarReceipt

KIS_KR_CALENDAR_BASE_URL: Final = "https://openapi.koreainvestment.com:9443"
_PATH: Final = "/uapi/domestic-stock/v1/quotations/chk-holiday"
_TR_ID: Final = "CTCA0903R"
_KST: Final = ZoneInfo("Asia/Seoul")
_MAX_REQUEST_SKEW: Final = dt.timedelta(seconds=2)


class UnsafeKisKrSessionCalendarEndpointError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR session calendar endpoint must be the official live origin"


class UnsafeKisKrSessionCalendarRedirectPolicyError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR session calendar client must not follow redirects"


class KisKrSessionCalendarTransportError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "KIS KR session calendar read-only transport failed"


class KisKrSessionCalendarFetchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base_date: dt.date
    requested_at: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if not _aware(self.requested_at) or self.requested_at.astimezone(_KST).date() != self.base_date:
            raise KisKrSessionCalendarTransportError
        return self


@final
class KisKrSessionCalendarClient:
    def __init__(
        self,
        client: httpx2.Client,
        credentials: KisCredentials,
        access_token: str,
        *,
        _clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    ) -> None:
        if str(client.base_url).rstrip("/") != KIS_KR_CALENDAR_BASE_URL:
            raise UnsafeKisKrSessionCalendarEndpointError
        if client.follow_redirects:
            raise UnsafeKisKrSessionCalendarRedirectPolicyError
        if type(access_token) is not str or not access_token:
            raise KisKrSessionCalendarTransportError
        self._client = client
        self._credentials = credentials
        self._access_token = access_token
        self._clock = _clock

    def fetch(self, source: KisKrSessionCalendarFetchRequest) -> KisKrSessionCalendarReceipt:
        request = _validated_request(source)
        started_at = self._clock()
        if not _aware(started_at) or abs(started_at - request.requested_at) > _MAX_REQUEST_SKEW:
            raise KisKrSessionCalendarTransportError
        try:
            response = self._client.get(
                _PATH,
                params=_params(request),
                headers=quote_headers(self._credentials, self._access_token, _TR_ID),
            )
            received_at = self._clock()
        except httpx2.HTTPError:
            raise KisKrSessionCalendarTransportError from None
        payload = bytes(response.content)
        if (
            not _aware(received_at)
            or received_at < started_at
            or not payload
            or _content_type(response) != "application/json"
        ):
            raise KisKrSessionCalendarTransportError
        return KisKrSessionCalendarReceipt(
            base_date=request.base_date,
            received_at=received_at,
            status_code=response.status_code,
            content_type="application/json",
            raw_payload=payload,
        )


def _validated_request(source: KisKrSessionCalendarFetchRequest) -> KisKrSessionCalendarFetchRequest:
    try:
        return KisKrSessionCalendarFetchRequest.model_validate(source.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise KisKrSessionCalendarTransportError from None


def _params(request: KisKrSessionCalendarFetchRequest) -> Mapping[str, str]:
    return {
        "BASS_DT": request.base_date.strftime("%Y%m%d"),
        "CTX_AREA_FK": "",
        "CTX_AREA_NK": "",
    }


def _content_type(response: httpx2.Response) -> str:
    return response.headers.get("content-type", "").partition(";")[0].strip().lower()


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
