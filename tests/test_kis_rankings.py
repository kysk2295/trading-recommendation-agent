from __future__ import annotations

import httpx2

from scr_backtest.kis_intraday import KisCredentials
from trading_agent import kis_rankings
from trading_agent.ranking_journal import RankingSource


def test_daytime_rankings_use_blue_ocean_exchange_codes() -> None:
    # Given/When: the dedicated KIS daytime exchange universe is inspected.
    exchanges = kis_rankings.DAYTIME_EXCHANGES

    # Then: it stays distinct from regular and premarket venue codes.
    assert exchanges == ("BAQ", "BAY", "BAA")
    assert set(exchanges).isdisjoint(kis_rankings.US_EXCHANGES)


def test_discovery_keeps_other_rankings_when_one_exchange_request_fails() -> None:
    # Given: AMEX up/down ranking is unavailable while the other requests work.
    requests: list[tuple[str, str]] = []
    waits: list[float] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        exchange = request.url.params["EXCD"]
        source = "updown" if request.url.path.endswith("updown-rate") else "volume"
        requests.append((source, exchange))
        if source == "updown" and exchange == "AMS":
            return httpx2.Response(500, request=request)
        return httpx2.Response(
            200,
            json={
                "rt_cd": "0",
                "msg_cd": "MCA00000",
                "msg1": "정상처리",
                "output2": [],
            },
        )

    # When: the six regular-market ranking requests are discovered.
    with httpx2.Client(
        base_url="https://openapi.koreainvestment.com:9443",
        transport=httpx2.MockTransport(handle),
    ) as client:
        discovery = kis_rankings.discover_rankings(
            client,
            KisCredentials("key", "secret"),
            "token",
            waits.append,
        )

    # Then: five successful groups remain and the missing source is explicit.
    assert len(requests) == 8
    assert requests.count(("updown", "AMS")) == 3
    assert len(waits) == 6
    assert len(discovery.groups) == 5
    assert discovery.failures[0].source is RankingSource.UPDOWN
    assert discovery.failures[0].exchange == "AMS"
    assert discovery.failures[0].reason == "HTTP 500"
