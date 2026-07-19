from __future__ import annotations

import datetime as dt

import httpx2
import pytest

from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_kr_market_client import (
    KIS_KR_MARKET_BASE_URL,
    KisKrMarketClient,
    KisKrMarketFetchRequest,
    KisKrMarketTransportError,
    UnsafeKisKrMarketEndpointError,
    UnsafeKisKrMarketRedirectPolicyError,
)
from trading_agent.kis_kr_market_models import KisKrMarketReceiptKind

SEOUL = dt.timezone(dt.timedelta(hours=9))
REQUESTED = dt.datetime(2026, 7, 20, 9, 4, 2, tzinfo=SEOUL)
TOKEN = "dummy-token"


@pytest.mark.parametrize(
    ("kind", "path", "tr_id"),
    (
        (
            KisKrMarketReceiptKind.MINUTE_BARS,
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            "FHKST03010200",
        ),
        (
            KisKrMarketReceiptKind.PRICE_STATUS,
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
        ),
        (
            KisKrMarketReceiptKind.ORDER_BOOK,
            "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            "FHKST01010200",
        ),
    ),
)
def test_client_fetches_only_reviewed_live_get_contracts(
    kind: KisKrMarketReceiptKind,
    path: str,
    tr_id: str,
) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            content=b'{"rt_cd":"0"}',
        )

    with httpx2.Client(
        base_url=KIS_KR_MARKET_BASE_URL,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    ) as http_client:
        client = KisKrMarketClient(http_client, _credentials(), TOKEN, _clock=lambda: REQUESTED)
        receipt = client.fetch(_request(kind))

    assert receipt.kind is kind
    assert receipt.received_at == REQUESTED
    assert len(seen) == 1
    assert seen[0].method == "GET"
    assert seen[0].url.path == path
    assert seen[0].headers["tr_id"] == tr_id
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert seen[0].url.params["FID_COND_MRKT_DIV_CODE"] == "J"
    assert seen[0].url.params["FID_INPUT_ISCD"] == "005930"
    if kind is KisKrMarketReceiptKind.MINUTE_BARS:
        assert seen[0].url.params["FID_INPUT_HOUR_1"] == "090300"
        assert seen[0].url.params["FID_PW_DATA_INCU_YN"] == "Y"


def test_client_rejects_unsafe_origin_redirect_and_future_minute_before_get() -> None:
    with (
        httpx2.Client(base_url="https://example.com", follow_redirects=False) as wrong_origin,
        pytest.raises(UnsafeKisKrMarketEndpointError),
    ):
        _ = KisKrMarketClient(wrong_origin, _credentials(), TOKEN)
    with (
        httpx2.Client(base_url=KIS_KR_MARKET_BASE_URL, follow_redirects=True) as redirecting,
        pytest.raises(UnsafeKisKrMarketRedirectPolicyError),
    ):
        _ = KisKrMarketClient(redirecting, _credentials(), TOKEN)

    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(200, content=b"{}")

    with httpx2.Client(
        base_url=KIS_KR_MARKET_BASE_URL,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    ) as http_client:
        client = KisKrMarketClient(http_client, _credentials(), TOKEN, _clock=lambda: REQUESTED)
        future = _request(KisKrMarketReceiptKind.MINUTE_BARS).model_copy(
            update={"minute_end_at": REQUESTED + dt.timedelta(minutes=1)}
        )
        with pytest.raises(KisKrMarketTransportError):
            _ = client.fetch(future)
    assert calls == 0

    with httpx2.Client(
        base_url=KIS_KR_MARKET_BASE_URL,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    ) as http_client:
        client = KisKrMarketClient(http_client, _credentials(), TOKEN, _clock=lambda: REQUESTED)
        stale = _request(KisKrMarketReceiptKind.PRICE_STATUS).model_copy(
            update={"requested_at": REQUESTED - dt.timedelta(seconds=3)}
        )
        with pytest.raises(KisKrMarketTransportError):
            _ = client.fetch(stale)
    assert calls == 0


def test_transport_error_does_not_expose_provider_or_credentials() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadError("PRIVATE provider appsecret detail", request=request)

    with httpx2.Client(
        base_url=KIS_KR_MARKET_BASE_URL,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    ) as http_client:
        client = KisKrMarketClient(http_client, _credentials(), TOKEN, _clock=lambda: REQUESTED)
        with pytest.raises(KisKrMarketTransportError) as captured:
            _ = client.fetch(_request(KisKrMarketReceiptKind.PRICE_STATUS))

    assert str(captured.value) == "KIS KR market read-only transport failed"
    assert "PRIVATE" not in str(captured.value)


def _request(kind: KisKrMarketReceiptKind) -> KisKrMarketFetchRequest:
    minute_end = REQUESTED.replace(second=0) - dt.timedelta(minutes=1)
    return KisKrMarketFetchRequest(
        kind=kind,
        symbol="005930",
        requested_at=REQUESTED,
        minute_end_at=minute_end if kind is KisKrMarketReceiptKind.MINUTE_BARS else None,
    )


def _credentials() -> KisCredentials:
    return KisCredentials(app_key="dummy-app-key", app_secret="dummy-app-secret")
