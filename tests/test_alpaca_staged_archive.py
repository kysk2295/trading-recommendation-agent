from __future__ import annotations

import csv
import datetime as dt
import gzip
from dataclasses import replace
from pathlib import Path

import httpx2

from trading_agent.alpaca_bars import AlpacaBarsClient
from trading_agent.alpaca_daily_cache import AlpacaDailyRangeCache
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_scanner import AlpacaScannerConfig
from trading_agent.alpaca_staged import AlpacaStagedArchive, AlpacaStagedConfig

type JsonValue = str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]


def test_staged_archive_selects_without_using_cutoff_or_later_bars(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        timeframe = request.url.params["timeframe"]
        if timeframe == "1Day":
            return httpx2.Response(200, json=_daily_payload())
        start = request.url.params["start"]
        if start == "2026-06-12T08:00:00+00:00":
            return httpx2.Response(200, json=_scanner_payload())
        return httpx2.Response(200, json=_candidate_payload())

    config = _config()
    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(respond),
    ) as client:
        result = AlpacaStagedArchive(
            client=client,
            credentials=AlpacaCredentials("test-key", "test-secret"),
            output_dir=tmp_path,
            config=config,
        ).archive_session(dt.date(2026, 6, 12), ("FLAT", "MOVE"))

    assert result.selected_symbols == ("MOVE",)
    assert result.scanner_bar_count == 4
    assert result.candidate_bar_count == 1
    minute_requests = [request for request in requests if request.url.params["timeframe"] == "1Min"]
    assert minute_requests[0].url.params["end"] == "2026-06-12T13:30:00+00:00"
    assert minute_requests[1].url.params["symbols"] == "MOVE"
    assert minute_requests[1].url.params["start"] == "2026-06-12T13:30:00+00:00"
    with gzip.open(result.decisions_path, "rt", encoding="utf-8", newline="") as decision_file:
        rows = {row["symbol"]: row for row in csv.DictReader(decision_file)}
    assert rows["MOVE"]["selected"] == "True"
    assert rows["MOVE"]["last_timestamp"] == "2026-06-12T09:29:00-04:00"
    assert rows["FLAT"]["selected"] == "False"


def test_staged_archive_reuses_all_completed_batches_without_http(tmp_path: Path) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        timeframe = request.url.params["timeframe"]
        if timeframe == "1Day":
            return httpx2.Response(200, json=_daily_payload())
        if request.url.params["start"] == "2026-06-12T08:00:00+00:00":
            return httpx2.Response(200, json=_scanner_payload())
        return httpx2.Response(200, json=_candidate_payload())

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(respond),
    ) as client:
        _ = AlpacaStagedArchive(
            client,
            AlpacaCredentials("test-key", "test-secret"),
            tmp_path,
            _config(),
        ).archive_session(dt.date(2026, 6, 12), ("FLAT", "MOVE"))

    def reject_http(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(request.url)

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(reject_http),
    ) as client:
        result = AlpacaStagedArchive(
            client,
            AlpacaCredentials("test-key", "test-secret"),
            tmp_path,
            _config(),
        ).archive_session(dt.date(2026, 6, 12), ("FLAT", "MOVE"))

    assert result.selected_symbols == ("MOVE",)
    assert result.request_count == 3
    assert result.new_request_count == 0
    assert result.skipped_batch_count == 3


def test_staged_archive_keeps_decisions_for_different_scanner_configs(tmp_path: Path) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.params["timeframe"] == "1Day":
            return httpx2.Response(200, json=_daily_payload())
        if request.url.params["start"] == "2026-06-12T08:00:00+00:00":
            return httpx2.Response(200, json=_scanner_payload())
        return httpx2.Response(200, json=_candidate_payload())

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(respond),
    ) as client:
        first = AlpacaStagedArchive(
            client,
            AlpacaCredentials("test-key", "test-secret"),
            tmp_path,
            _config(),
        ).archive_session(dt.date(2026, 6, 12), ("FLAT", "MOVE"))

    stricter = replace(
        _config(),
        scanner=replace(_config().scanner, min_change_pct=100.0),
    )

    def reject_http(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(request.url)

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(reject_http),
    ) as client:
        second = AlpacaStagedArchive(
            client,
            AlpacaCredentials("test-key", "test-secret"),
            tmp_path,
            stricter,
        ).archive_session(dt.date(2026, 6, 12), ("FLAT", "MOVE"))

    assert first.decisions_path != second.decisions_path
    assert first.decisions_path.is_file()
    assert second.decisions_path.is_file()
    assert second.base_selected_symbols == ()
    assert second.selected_symbols == ("MOVE",)
    metadata = tuple((tmp_path / "staged_sessions/2026/06/12").glob("session_*.metadata.json"))
    assert len(metadata) == 2


def test_staged_archive_uses_range_cache_without_daily_api_request(tmp_path: Path) -> None:
    credentials = AlpacaCredentials("test-key", "test-secret")

    def daily_response(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_daily_payload())

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(daily_response),
    ) as client:
        cache = AlpacaDailyRangeCache(
            bars_client=AlpacaBarsClient(client, credentials, 0.0),
            output_dir=tmp_path / "range_cache",
            batch_size=100,
            lookback_calendar_days=45,
            reference_sessions=2,
            minimum_reference_sessions=2,
        )
        _ = cache.build(dt.date(2026, 6, 12), dt.date(2026, 6, 12), ("FLAT", "MOVE"))

    requests: list[httpx2.Request] = []

    def minute_response(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        assert request.url.params["timeframe"] == "1Min"
        if request.url.params["start"] == "2026-06-12T08:00:00+00:00":
            return httpx2.Response(200, json=_scanner_payload())
        return httpx2.Response(200, json=_candidate_payload())

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(minute_response),
    ) as client:
        result = AlpacaStagedArchive(
            client,
            credentials,
            tmp_path / "staged",
            _config(),
            daily_cache=cache,
        ).archive_session(dt.date(2026, 6, 12), ("FLAT", "MOVE"))

    assert result.selected_symbols == ("MOVE",)
    assert result.request_count == 2
    assert len(requests) == 2


def _config() -> AlpacaStagedConfig:
    return AlpacaStagedConfig(
        scanner_cutoff=dt.time(9, 30),
        scanner=AlpacaScannerConfig(
            min_change_pct=0.10,
            min_price=1.0,
            max_price=100.0,
            min_dollar_volume=100_000.0,
            min_adv_fraction=0.10,
            max_candidates=10,
        ),
        batch_size=100,
        request_interval_seconds=0.0,
        reference_lookback_calendar_days=45,
        reference_sessions=2,
        minimum_reference_sessions=2,
    )


def _daily_payload() -> dict[str, JsonValue]:
    bars: dict[str, list[dict[str, JsonValue]]] = {}
    for symbol in ("FLAT", "MOVE"):
        bars[symbol] = [
            _bar("2026-06-10T04:00:00Z", 10.0, 100_000),
            _bar("2026-06-11T04:00:00Z", 10.0, 100_000),
        ]
    return {"bars": bars, "next_page_token": None}


def _scanner_payload() -> dict[str, JsonValue]:
    return {
        "bars": {
            "FLAT": [_bar("2026-06-12T13:29:00Z", 10.0, 20_000)],
            "MOVE": [
                _bar("2026-06-12T13:28:00Z", 11.0, 10_000),
                _bar("2026-06-12T13:29:00Z", 12.0, 20_000),
                _bar("2026-06-12T13:30:00Z", 99.0, 9_000_000),
            ],
        },
        "next_page_token": None,
    }


def _candidate_payload() -> dict[str, JsonValue]:
    return {
        "bars": {"MOVE": [_bar("2026-06-12T13:30:00Z", 12.1, 50_000)]},
        "next_page_token": None,
    }


def _bar(timestamp: str, price: float, volume: int) -> dict[str, JsonValue]:
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
