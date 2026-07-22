from __future__ import annotations

import datetime as dt

import httpx2
import pytest

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_most_active import (
    AlpacaMostActiveClient,
    InvalidAlpacaMostActiveSourceError,
)
from trading_agent.us_equity_calendar import NEW_YORK

SESSION = dt.date(2026, 7, 22)
OBSERVED_AT = dt.datetime(2026, 7, 22, 16, 5, tzinfo=NEW_YORK)


def test_client_reads_ranked_volume_universe_from_market_data_only() -> None:
    # Given: an Alpaca screener response ordered by descending volume.
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "last_updated": "2026-07-22T20:05:00Z",
                "most_actives": [
                    {"symbol": "BETA", "volume": 2_000_000, "trade_count": 20_000},
                    {"symbol": "ACME", "volume": 1_000_000, "trade_count": 10_000},
                ],
            },
        )

    # When: the bounded screener client fetches two symbols.
    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(respond),
    ) as client:
        result = AlpacaMostActiveClient(client, AlpacaCredentials("key", "secret")).fetch(
            top=2,
            session_date=SESSION,
            observed_at=OBSERVED_AT,
        )

    # Then: rank evidence is retained and scanner symbols are canonicalized separately.
    assert tuple(item.symbol for item in result.most_actives) == ("BETA", "ACME")
    assert result.scanner_symbols == ("ACME", "BETA")
    assert requests[0].method == "GET"
    assert requests[0].url.path == "/v1beta1/screener/stocks/most-actives"
    assert requests[0].url.params["by"] == "volume"
    assert requests[0].url.params["top"] == "2"


def test_client_rejects_duplicate_or_non_descending_rank_evidence() -> None:
    # Given: malformed rank evidence with duplicate symbols and increasing volume.
    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "last_updated": "2026-07-22T20:05:00Z",
                "most_actives": [
                    {"symbol": "ACME", "volume": 1, "trade_count": 1},
                    {"symbol": "ACME", "volume": 2, "trade_count": 2},
                ],
            },
        )

    # When/Then: the client rejects the response instead of inventing a universe.
    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(respond),
    ) as client, pytest.raises(InvalidAlpacaMostActiveSourceError):
        _ = AlpacaMostActiveClient(client, AlpacaCredentials("key", "secret")).fetch(
            top=2,
            session_date=SESSION,
            observed_at=OBSERVED_AT,
        )


def test_client_rejects_a_screener_snapshot_from_another_ny_session() -> None:
    # Given: a structurally valid response last updated on the prior NY session.
    def respond(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "last_updated": "2026-07-21T20:05:00Z",
                "most_actives": [
                    {"symbol": "ACME", "volume": 1_000_000, "trade_count": 10_000},
                ],
            },
        )

    # When/Then: stale membership cannot become today's completed-day universe.
    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(respond),
    ) as client, pytest.raises(InvalidAlpacaMostActiveSourceError):
        _ = AlpacaMostActiveClient(client, AlpacaCredentials("key", "secret")).fetch(
            top=1,
            session_date=SESSION,
            observed_at=OBSERVED_AT,
        )
