from __future__ import annotations

import json

import httpx2

from scr_backtest.kis_intraday import (
    KisCredentials,
    KisMinuteRequest,
    KisProbeRequest,
    KisSession,
    collect_minute_pages,
    fetch_minute_page,
    issue_access_token,
    resolve_access_token,
)


def test_issue_access_token_posts_credentials_without_leaking_them() -> None:
    given_requests: list[httpx2.Request] = []
    given_credentials = KisCredentials(app_key="key", app_secret="secret")

    def handle(request: httpx2.Request) -> httpx2.Response:
        given_requests.append(request)
        return httpx2.Response(
            200,
            json={
                "access_token": "issued-token",
                "access_token_token_expired": "2026-07-14 00:00:00",
                "token_type": "Bearer",
                "expires_in": 86400,
            },
        )

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as given_client:
        when_token = issue_access_token(given_client, given_credentials)

    assert when_token == "issued-token"
    assert given_requests[0].url.path == "/oauth2/tokenP"
    assert json.loads(given_requests[0].content) == {
        "grant_type": "client_credentials",
        "appkey": "key",
        "appsecret": "secret",
    }


def test_fetch_minute_page_sends_read_only_us_stock_request() -> None:
    given_requests: list[httpx2.Request] = []
    given_credentials = KisCredentials(app_key="key", app_secret="secret")
    given_request = KisMinuteRequest(exchange="NAS", symbol="AAPL")

    def handle(request: httpx2.Request) -> httpx2.Response:
        given_requests.append(request)
        return httpx2.Response(
            200,
            json={
                "rt_cd": "0",
                "msg_cd": "MCA00000",
                "msg1": "정상처리 되었습니다.",
                "output1": {},
                "output2": [
                    {
                        "tymd": "20260710",
                        "xymd": "20260710",
                        "xhms": "175000",
                        "kymd": "20260711",
                        "khms": "065000",
                        "open": "315.02",
                        "high": "315.18",
                        "low": "315.02",
                        "last": "315.18",
                        "evol": "24",
                        "eamt": "7564",
                    }
                ],
            },
        )

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as given_client:
        when_bars = fetch_minute_page(
            given_client,
            given_credentials,
            access_token="issued-token",
            request=given_request,
        )

    assert when_bars[0].close == 315.18
    assert given_requests[0].method == "GET"
    assert given_requests[0].url.path.endswith("/inquire-time-itemchartprice")
    assert given_requests[0].url.params["EXCD"] == "NAS"
    assert given_requests[0].url.params["NREC"] == "120"
    assert given_requests[0].headers["authorization"] == "Bearer issued-token"
    assert given_requests[0].headers["tr_id"] == "HHDFS76950200"


def test_collect_minute_pages_moves_cursor_back_without_duplicates() -> None:
    given_requests: list[httpx2.Request] = []
    given_session = KisSession(
        credentials=KisCredentials(app_key="key", app_secret="secret"),
        access_token="issued-token",
    )
    given_probe = KisProbeRequest(
        minute=KisMinuteRequest(exchange="NAS", symbol="AAPL"),
        page_count=2,
    )

    def handle(request: httpx2.Request) -> httpx2.Response:
        given_requests.append(request)
        time = "175000" if request.url.params["KEYB"] == "" else "174900"
        return httpx2.Response(
            200,
            json={
                "rt_cd": "0",
                "msg_cd": "MCA00000",
                "msg1": "정상처리 되었습니다.",
                "output1": {},
                "output2": [
                    {
                        "xymd": "20260710",
                        "xhms": time,
                        "kymd": "20260711",
                        "khms": "064900" if time == "174900" else "065000",
                        "open": "315.02",
                        "high": "315.18",
                        "low": "315.02",
                        "last": "315.18",
                        "evol": "24",
                        "eamt": "7564",
                    }
                ],
            },
        )

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as given_client:
        when_bars = collect_minute_pages(given_client, given_session, given_probe)

    assert tuple(bar.exchange_timestamp.minute for bar in when_bars) == (49, 50)
    assert given_requests[1].url.params["KEYB"] == "20260710174900"
    assert "issued-token" not in repr(given_session)


def test_resolve_access_token_reuses_environment_token_without_http() -> None:
    given_credentials = KisCredentials(app_key="key", app_secret="secret")

    def reject_http(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(request.url)

    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(reject_http),
    ) as given_client:
        when_token = resolve_access_token(
            given_client,
            given_credentials,
            {"KIS_ACCESS_TOKEN": "existing-token"},
        )

    assert when_token == "existing-token"
