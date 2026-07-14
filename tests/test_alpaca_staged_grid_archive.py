from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx2

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_scanner import AlpacaScannerConfig
from trading_agent.alpaca_staged import AlpacaStagedArchive, AlpacaStagedConfig


def test_staged_archive_preserves_top_ten_paths_for_adjacent_scanner_configs(
    tmp_path: Path,
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.params["timeframe"] == "1Day":
            return httpx2.Response(
                200,
                json={
                    "bars": {
                        symbol: [
                            _bar("2026-06-10T04:00:00Z", 10.0, 100_000),
                            _bar("2026-06-11T04:00:00Z", 10.0, 100_000),
                        ]
                        for symbol in ("EXPENSIVE", "LOW")
                    },
                    "next_page_token": None,
                },
            )
        if request.url.params["start"] == "2026-06-12T08:00:00+00:00":
            return httpx2.Response(
                200,
                json={
                    "bars": {
                        "EXPENSIVE": [_bar("2026-06-12T13:29:00Z", 30.0, 20_000)],
                        "LOW": [_bar("2026-06-12T13:29:00Z", 12.0, 50_000)],
                    },
                    "next_page_token": None,
                },
            )
        return httpx2.Response(
            200,
            json={
                "bars": {
                    symbol: [_bar("2026-06-12T13:30:00Z", price, 10_000)]
                    for symbol, price in (("EXPENSIVE", 30.1), ("LOW", 12.1))
                },
                "next_page_token": None,
            },
        )

    config = AlpacaStagedConfig(
        scanner=AlpacaScannerConfig(
            min_change_pct=0.02,
            min_price=1.0,
            max_price=100.0,
            min_dollar_volume=100_000.0,
            min_adv_fraction=0.10,
            max_candidates=1,
        ),
        batch_size=100,
        request_interval_seconds=0.0,
        reference_sessions=2,
        minimum_reference_sessions=2,
    )
    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(respond),
    ) as client:
        result = AlpacaStagedArchive(
            client,
            AlpacaCredentials("test-key", "test-secret"),
            tmp_path,
            config,
        ).archive_session(dt.date(2026, 6, 12), ("EXPENSIVE", "LOW"))

    assert result.base_selected_symbols == ("EXPENSIVE",)
    assert result.selected_symbols == ("EXPENSIVE", "LOW")
    minute_requests = [request for request in requests if request.url.params["timeframe"] == "1Min"]
    assert minute_requests[1].url.params["symbols"] == "EXPENSIVE,LOW"
    metadata_path = next((tmp_path / "staged_sessions/2026/06/12").glob("session_*.metadata.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["base_selected_symbols"] == ["EXPENSIVE"]
    assert metadata["selected_symbols"] == ["EXPENSIVE", "LOW"]
    assert metadata["scanner_grid_config_count"] == 108
    assert metadata["scanner_grid_portfolio_limit"] == 10


def _bar(timestamp: str, price: float, volume: int) -> dict[str, str | int | float]:
    return {
        "t": timestamp,
        "o": price,
        "h": price,
        "l": price,
        "c": price,
        "v": volume,
        "n": 1,
        "vw": price,
    }
