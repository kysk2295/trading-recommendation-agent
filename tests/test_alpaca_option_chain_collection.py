from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx2

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_option_chain_client import AlpacaOptionChainClient
from trading_agent.alpaca_option_chain_collection import (
    collect_alpaca_option_chain,
)
from trading_agent.alpaca_option_chain_models import (
    OptionChainFailure,
    OptionChainRequest,
    OptionChainStatus,
    OptionContractType,
    OptionFeed,
)
from trading_agent.alpaca_option_chain_store import AlpacaOptionChainStore

STARTED = dt.datetime(2026, 7, 23, 14, 30, tzinfo=dt.UTC)
RECEIVED = STARTED + dt.timedelta(seconds=1)
COMPLETED = STARTED + dt.timedelta(seconds=2)
CREDENTIALS = AlpacaCredentials("test-key", "test-secret")


def _request() -> OptionChainRequest:
    return OptionChainRequest(
        collection_id="m6-http-failure",
        underlying_symbol="AAPL",
        feed=OptionFeed.INDICATIVE,
        expiration_date=dt.date(2026, 7, 24),
        contract_type=OptionContractType.CALL,
        limit=100,
        max_pages=2,
    )


def test_http_failure_is_preserved_raw_before_failed_terminal(
    tmp_path: Path,
) -> None:
    # Given the exact Alpaca endpoint returns a non-JSON service failure.
    raw_payload = b"service unavailable"

    def handle(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            503,
            request=request,
            headers={"content-type": "text/plain"},
            stream=httpx2.ByteStream(raw_payload),
        )

    store = AlpacaOptionChainStore(tmp_path / "option-chain.sqlite3")
    store.preflight_write()

    # When the collector receives the failed provider response.
    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(handle),
        follow_redirects=False,
    ) as http_client:
        result = collect_alpaca_option_chain(
            AlpacaOptionChainClient(
                http_client,
                CREDENTIALS,
                _clock=lambda: RECEIVED,
            ),
            store,
            _request(),
            _clock=iter((STARTED, COMPLETED)).__next__,
        )

    # Then the wire bytes exist and the terminal records HTTP failure.
    assert result.run.status is OptionChainStatus.FAILED
    assert result.run.failure_code is OptionChainFailure.HTTP_STATUS
    assert store.counts() == (1, 1)
    assert store.receipts(_request().request_id)[0].raw_payload == raw_payload


def test_paginated_chain_is_terminally_replayed_without_network(
    tmp_path: Path,
) -> None:
    # Given two bounded pages with an opaque provider page token.
    calls: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        page_token = request.url.params.get("page_token")
        symbol = (
            "AAPL260724C00200000"
            if page_token is None
            else "AAPL260724C00210000"
        )
        payload = json.dumps(
            {
                "snapshots": {symbol: {}},
                "next_page_token": "opaque-token" if page_token is None else None,
            },
            separators=(",", ":"),
        ).encode()
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            stream=httpx2.ByteStream(payload),
        )

    store = AlpacaOptionChainStore(tmp_path / "option-chain.sqlite3")
    store.preflight_write()
    clock = iter(
        (
            RECEIVED,
            RECEIVED + dt.timedelta(seconds=1),
            STARTED,
            COMPLETED,
        )
    ).__next__
    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(handle),
        follow_redirects=False,
    ) as http_client:
        fetcher = AlpacaOptionChainClient(
            http_client,
            CREDENTIALS,
            _clock=clock,
        )

        # When collection completes and the exact request is repeated.
        collected = collect_alpaca_option_chain(
            fetcher,
            store,
            _request(),
            _clock=clock,
        )
        replayed = collect_alpaca_option_chain(fetcher, store, _request())

    # Then the second call is local-only and the GET contract is exact.
    assert collected.replayed is False
    assert replayed.replayed is True
    assert replayed.run == collected.run
    assert len(calls) == 2
    assert calls[0].method == "GET"
    assert calls[0].url.path == "/v1beta1/options/snapshots/AAPL"
    assert dict(calls[0].url.params) == {
        "expiration_date": "2026-07-24",
        "feed": "indicative",
        "limit": "100",
        "type": "call",
    }
    assert calls[1].url.params["page_token"] == "opaque-token"
    assert calls[0].headers["accept-encoding"] == "identity"
    assert store.counts() == (2, 1)
