from __future__ import annotations

import datetime as dt
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path

import httpx2
import pytest

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_security_master import collect_alpaca_security_master
from trading_agent.alpaca_security_master_models import AlpacaSecurityMasterError
from trading_agent.alpaca_security_master_store import AlpacaSecurityMasterStore
from trading_agent.security_master_models import AssetClass, DataMarketDomain

OBSERVED_AT = dt.datetime(2026, 7, 19, 3, 0, tzinfo=dt.UTC)
ASSETS = (
    {
        "id": "asset-live",
        "class": "us_equity",
        "exchange": "NASDAQ",
        "symbol": "FIXT",
        "name": "Fixture Inc",
        "status": "active",
        "tradable": True,
        "marginable": True,
        "maintenance_margin_requirement": 30,
        "shortable": True,
        "easy_to_borrow": True,
        "fractionable": True,
        "attributes": [],
        "borrow_status": "easy_to_borrow",
        "margin_requirement_long": "30",
        "margin_requirement_short": "30",
    },
    {
        "id": "asset-dead",
        "class": "us_equity",
        "exchange": "NYSE",
        "symbol": "DEAD",
        "name": "Dead Inc",
        "status": "inactive",
        "tradable": False,
    },
    {
        "id": "asset-otc",
        "class": "us_equity",
        "exchange": "OTC",
        "symbol": "PINK",
        "name": "Pink Inc",
        "status": "active",
        "tradable": False,
    },
)
BODY = json.dumps(ASSETS, separators=(",", ":")).encode()
DUPLICATE_BODY = json.dumps(
    (
        {**ASSETS[0], "id": "one", "symbol": "DUP"},
        {**ASSETS[0], "id": "two", "exchange": "NYSE", "symbol": "DUP"},
    ),
    separators=(",", ":"),
).encode()


def test_raw_first_asset_collection_projects_current_security_master(
    tmp_path: Path,
) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, content=BODY)

    store = AlpacaSecurityMasterStore(tmp_path / "security-master.sqlite3")
    with _client(handle) as client:
        snapshot = collect_alpaca_security_master(
            client,
            AlpacaCredentials("fixture-key", "fixture-secret"),
            store,
            observed_at=OBSERVED_AT,
        )

    assert requests[0].url.path == "/v2/assets"
    assert requests[0].url.params["status"] == "all"
    assert requests[0].url.params["asset_class"] == "us_equity"
    assert requests[0].headers["APCA-API-KEY-ID"] == "fixture-key"
    assert store.raw_count() == 1
    assert store.snapshot_count() == 1
    assert store.latest_snapshot() == snapshot
    assert tuple(item.value for item in snapshot.instruments) == ("alpaca:asset-live",)
    assert snapshot.instruments[0].market_domain is DataMarketDomain.US_EQUITIES
    assert snapshot.instruments[0].asset_class is AssetClass.EQUITY
    assert snapshot.instruments[0].venue == "XNAS"
    assert snapshot.instruments[0].valid_from == OBSERVED_AT
    assert snapshot.aliases[0].value == "FIXT"
    assert snapshot.aliases[0].effective_from == OBSERVED_AT
    assert store.raw_payload(snapshot.raw_receipt_id) == BODY
    assert stat.S_IMODE(os.stat(store.path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(f"{store.path}.writer.lock").st_mode) == 0o600


def test_exact_collection_retry_reuses_raw_and_snapshot(tmp_path: Path) -> None:
    store = AlpacaSecurityMasterStore(tmp_path / "security-master.sqlite3")
    with _client(lambda request: httpx2.Response(200, content=BODY)) as client:
        first = collect_alpaca_security_master(
            client,
            AlpacaCredentials("fixture-key", "fixture-secret"),
            store,
            observed_at=OBSERVED_AT,
        )
        second = collect_alpaca_security_master(
            client,
            AlpacaCredentials("fixture-key", "fixture-secret"),
            store,
            observed_at=OBSERVED_AT,
        )

    assert second == first
    assert store.raw_count() == 1
    assert store.snapshot_count() == 1


@pytest.mark.parametrize(
    "body",
    (
        b"not-json",
        DUPLICATE_BODY,
    ),
)
def test_invalid_asset_response_preserves_raw_but_blocks_snapshot(
    tmp_path: Path,
    body: bytes,
) -> None:
    store = AlpacaSecurityMasterStore(tmp_path / "security-master.sqlite3")
    with (
        _client(lambda request: httpx2.Response(200, content=body)) as client,
        pytest.raises(
            AlpacaSecurityMasterError,
            match="Alpaca security master is invalid",
        ),
    ):
        _ = collect_alpaca_security_master(
            client,
            AlpacaCredentials("fixture-key", "fixture-secret"),
            store,
            observed_at=OBSERVED_AT,
        )

    assert store.raw_count() == 1
    assert store.snapshot_count() == 0


def test_noncanonical_trading_origin_is_blocked_before_http(tmp_path: Path) -> None:
    called = False

    def handle(request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(200, content=BODY)

    store = AlpacaSecurityMasterStore(tmp_path / "security-master.sqlite3")
    with (
        httpx2.Client(
            base_url="https://api.alpaca.markets",
            transport=httpx2.MockTransport(handle),
            follow_redirects=False,
        ) as client,
        pytest.raises(AlpacaSecurityMasterError),
    ):
        _ = collect_alpaca_security_master(
            client,
            AlpacaCredentials("fixture-key", "fixture-secret"),
            store,
            observed_at=OBSERVED_AT,
        )

    assert called is False
    assert store.raw_count() == 0


def _client(
    handler: Callable[[httpx2.Request], httpx2.Response],
) -> httpx2.Client:
    return httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handler),
        follow_redirects=False,
    )
