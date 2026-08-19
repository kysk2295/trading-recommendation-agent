from __future__ import annotations

import datetime as dt
import json

import httpx2

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.us_strategy_research_http import AlpacaUsStrategyResearchClient


def test_fetches_only_sip_bars_and_latest_quotes() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/v2/stocks/bars":
            return httpx2.Response(
                200,
                json={
                    "bars": {
                        "SPY": [
                            {
                                "t": "2026-08-19T13:39:00Z",
                                "o": 501,
                                "h": 502,
                                "l": 500,
                                "c": 501.5,
                                "v": 1,
                                "n": 1,
                                "vw": 501.4,
                            },
                            {
                                "t": "2026-08-19T13:40:00Z",
                                "o": 501.5,
                                "h": 503,
                                "l": 501,
                                "c": 502.5,
                                "v": 1,
                                "n": 1,
                                "vw": 502.4,
                            },
                        ]
                    },
                    "next_page_token": None,
                },
            )
        return httpx2.Response(
            200,
            content=json.dumps({"quotes": {"SPY": {"ap": 502.51, "bp": 502.49, "t": "2026-08-19T13:41:55Z"}}}).encode(),
        )

    with httpx2.Client(transport=httpx2.MockTransport(handler), base_url="https://data.alpaca.markets") as http:
        bars, quotes = AlpacaUsStrategyResearchClient(
            http,
            AlpacaCredentials(key_id="test", secret_key="test"),
        ).fetch(("SPY",), dt.datetime(2026, 8, 19, 13, 42, tzinfo=dt.UTC))

    assert len(bars["SPY"]) == 2
    assert quotes["SPY"].bid == 502.49
    assert [request.method for request in requests] == ["GET", "GET"]
    assert requests[0].url.params["feed"] == "sip"
    assert requests[1].url.path == "/v2/stocks/quotes/latest"
    assert requests[1].url.params["feed"] == "sip"
