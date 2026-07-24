from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx2
import pytest

from scr_backtest.kis_intraday import KisCredentials
from trading_agent.kis_overseas_futures_client import (
    KIS_OVERSEAS_FUTURES_BASE_URL,
    KisOverseasFuturesClient,
)
from trading_agent.kis_overseas_futures_collection import (
    collect_kis_overseas_futures_quotes,
)
from trading_agent.kis_overseas_futures_models import (
    KisFuturesQuoteFailure,
    KisFuturesQuoteRawResponse,
    KisFuturesQuoteRequest,
    KisFuturesQuoteStatus,
)
from trading_agent.kis_overseas_futures_store import KisOverseasFuturesStore

RECEIVED_AT = dt.datetime(2026, 7, 24, 4, 30, tzinfo=dt.UTC)


class _Fetcher:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.calls: list[str] = []

    def fetch(
        self,
        request: KisFuturesQuoteRequest,
        symbol: str,
    ) -> KisFuturesQuoteRawResponse:
        self.calls.append(symbol)
        return KisFuturesQuoteRawResponse(
            request_id=request.request_id,
            symbol=symbol,
            received_at=RECEIVED_AT,
            status_code=200,
            content_type="application/json",
            raw_payload=self.payloads[symbol],
        )


def test_collection_preserves_two_contract_quotes_and_replays_without_fetch(
    tmp_path: Path,
) -> None:
    request = _request()
    fetcher = _Fetcher(
        {
            "ESU26": _payload(expiration="20260918", last_price="6425.25"),
            "ESZ26": _payload(expiration="20261218", last_price="6460.50"),
        }
    )
    store = KisOverseasFuturesStore(tmp_path / "quotes.sqlite3")

    first = collect_kis_overseas_futures_quotes(fetcher, store, request)
    replay = collect_kis_overseas_futures_quotes(fetcher, store, request)

    assert first.run.status is KisFuturesQuoteStatus.SUCCESS
    assert [item.symbol for item in first.run.quotes] == ["ESU26", "ESZ26"]
    assert first.run.quotes[0].expiration_date == dt.date(2026, 9, 18)
    assert first.run.quotes[0].last_price == 6425.25
    assert first.run.quotes[1].bid_price == 6459.75
    assert fetcher.calls == ["ESU26", "ESZ26"]
    assert replay.replayed
    assert store.counts() == (2, 1)


def test_invalid_quote_is_raw_first_and_terminally_failed(tmp_path: Path) -> None:
    request = _request()
    malformed = _payload(
        expiration="20260918",
        last_price="6425.25",
        bid_price="6426.00",
        ask_price="6425.50",
    )
    fetcher = _Fetcher({"ESU26": malformed, "ESZ26": malformed})
    store = KisOverseasFuturesStore(tmp_path / "quotes.sqlite3")

    result = collect_kis_overseas_futures_quotes(fetcher, store, request)

    assert result.run.status is KisFuturesQuoteStatus.FAILED
    assert result.run.failure is KisFuturesQuoteFailure.RESPONSE_STRUCTURE
    assert result.run.quotes == ()
    assert store.counts() == (1, 1)
    assert store.receipt(request.request_id, "ESU26") is not None


def test_client_uses_only_official_read_only_contract() -> None:
    observed: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        observed.append(request)
        return httpx2.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=_payload(expiration="20260918", last_price="6425.25"),
        )

    with httpx2.Client(
        base_url=KIS_OVERSEAS_FUTURES_BASE_URL,
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    ) as http_client:
        client = KisOverseasFuturesClient(
            http_client,
            KisCredentials(app_key="app", app_secret="secret"),
            "token",
            _clock=lambda: RECEIVED_AT,
        )
        response = client.fetch(_request(), "ESU26")

    assert response.status_code == 200
    assert len(observed) == 1
    assert observed[0].url.path == (
        "/uapi/overseas-futureoption/v1/quotations/inquire-price"
    )
    assert observed[0].url.params["SRS_CD"] == "ESU26"
    assert observed[0].headers["tr_id"] == "HHDFC55010000"
    assert observed[0].method == "GET"


def test_client_rejects_nonofficial_base_url() -> None:
    with httpx2.Client(
        base_url="https://example.invalid",
        transport=httpx2.MockTransport(lambda request: httpx2.Response(200)),
        follow_redirects=False,
    ) as client, pytest.raises(ValueError):
        KisOverseasFuturesClient(
            client,
            KisCredentials(app_key="app", app_secret="secret"),
            "token",
        )


def _request() -> KisFuturesQuoteRequest:
    return KisFuturesQuoteRequest(
        root_symbol="ES",
        symbols=("ESU26", "ESZ26"),
    )


def _payload(
    *,
    expiration: str,
    last_price: str,
    bid_price: str = "6459.75",
    ask_price: str = "6460.25",
) -> bytes:
    return json.dumps(
        {
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "정상처리 되었습니다.",
            "output1": {
                "proc_date": "20260724",
                "proc_time": "043000",
                "last_price": last_price,
                "vol": "123456",
                "bid_price": bid_price,
                "ask_price": ask_price,
                "prev_price": "6400.00",
                "exch_cd": "CME",
                "crc_cd": "USD",
                "trd_fr_date": "20250317",
                "expr_date": expiration,
                "trd_to_date": expiration,
                "sbsnsdate": "20260723",
                "sttl_price": "6401.25",
            },
        },
        separators=(",", ":"),
    ).encode()
