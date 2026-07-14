from __future__ import annotations

from pathlib import Path

import httpx2

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_universe import fetch_alpaca_universe, write_universe_snapshot


def test_fetch_alpaca_universe_keeps_listed_active_and_inactive_assets(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json=[
                {
                    "id": "1",
                    "class": "us_equity",
                    "exchange": "NASDAQ",
                    "symbol": "LIVE",
                    "name": "Live Inc",
                    "status": "active",
                    "tradable": True,
                },
                {
                    "id": "2",
                    "class": "us_equity",
                    "exchange": "NYSE",
                    "symbol": "DEAD",
                    "name": "Delisted Inc",
                    "status": "inactive",
                    "tradable": False,
                },
                {
                    "id": "3",
                    "class": "us_equity",
                    "exchange": "OTC",
                    "symbol": "PINK",
                    "name": "OTC Inc",
                    "status": "active",
                    "tradable": False,
                },
                {
                    "id": "4",
                    "class": "us_equity",
                    "exchange": "NASDAQ",
                    "symbol": "0029900E0",
                    "name": "Contra security",
                    "status": "inactive",
                    "tradable": False,
                },
                {
                    "id": "5",
                    "class": "us_equity",
                    "exchange": "NYSE",
                    "symbol": "OLD_DELISTED",
                    "name": "Synthetic delisted alias",
                    "status": "inactive",
                    "tradable": False,
                },
                {
                    "id": "6",
                    "class": "us_equity",
                    "exchange": "NYSE",
                    "symbol": "BRK.B",
                    "name": "Berkshire Hathaway Inc.",
                    "status": "active",
                    "tradable": True,
                },
                {
                    "id": "7",
                    "class": "us_equity",
                    "exchange": "NASDAQ",
                    "symbol": "B002455",
                    "name": "Corporate-action identifier",
                    "status": "inactive",
                    "tradable": False,
                },
            ],
        )

    with httpx2.Client(
        base_url="https://paper-api.alpaca.markets",
        transport=httpx2.MockTransport(handle),
    ) as client:
        assets = fetch_alpaca_universe(
            client,
            AlpacaCredentials("test-key", "test-secret"),
        )
    write_universe_snapshot(tmp_path / "universe.csv", assets)

    assert tuple(asset.symbol for asset in assets) == ("BRK.B", "DEAD", "LIVE")
    assert requests[0].url.params["status"] == "all"
    assert requests[0].headers["APCA-API-KEY-ID"] == "test-key"
    snapshot = (tmp_path / "universe.csv").read_text(encoding="utf-8")
    assert "DEAD,inactive" in snapshot
    assert "PINK" not in snapshot
    assert "0029900E0" not in snapshot
    assert "B002455" not in snapshot
    assert "OLD_DELISTED" not in snapshot
