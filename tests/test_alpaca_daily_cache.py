from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import httpx2
import pytest

from trading_agent.alpaca_bars import AlpacaBarsClient
from trading_agent.alpaca_daily_cache import AlpacaDailyRangeCache
from trading_agent.alpaca_http import AlpacaCredentials


def test_daily_range_cache_excludes_target_day_and_reuses_completed_batch(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "bars": {
                    "MOVE": [
                        _bar("2026-06-10T04:00:00Z", 10.0, 100_000),
                        _bar("2026-06-11T04:00:00Z", 12.0, 200_000),
                        _bar("2026-06-12T04:00:00Z", 99.0, 9_000_000),
                    ]
                },
                "next_page_token": None,
            },
        )

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(respond),
    ) as client:
        cache = _cache(tmp_path, client)
        first = cache.build(dt.date(2026, 6, 12), dt.date(2026, 6, 12), ("MOVE",))

    assert requests[0].url.params["timeframe"] == "1Day"
    assert requests[0].url.params["end"] == "2026-06-11"
    references = cache.references_for_session(dt.date(2026, 6, 12), ("MOVE",))
    assert references[0].prior_session == dt.date(2026, 6, 11)
    assert references[0].prior_close == 12.0
    assert references[0].average_volume == 150_000.0
    assert references[0].history_sessions == 2
    with sqlite3.connect(first.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM daily_bars").fetchone() == (2,)
        assert connection.execute("SELECT value FROM cache_metadata WHERE name = 'status'").fetchone() == ("complete",)
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = 'daily_bars_session_date'"
        ).fetchone() == (1,)
    with pytest.raises(ValueError, match="캐시 밖"):
        _ = cache.references_for_session(dt.date(2026, 6, 13), ("MOVE",))

    def reject_http(request: httpx2.Request) -> httpx2.Response:
        raise AssertionError(request.url)

    with httpx2.Client(
        base_url="https://data.alpaca.markets",
        transport=httpx2.MockTransport(reject_http),
    ) as client:
        second = _cache(tmp_path, client).build(
            dt.date(2026, 6, 12),
            dt.date(2026, 6, 12),
            ("MOVE",),
        )

    assert second.request_count == 1
    assert second.new_request_count == 0
    assert second.skipped_batch_count == 1
    assert second.bar_count == 2


def _cache(tmp_path: Path, client: httpx2.Client) -> AlpacaDailyRangeCache:
    return AlpacaDailyRangeCache(
        bars_client=AlpacaBarsClient(
            client=client,
            credentials=AlpacaCredentials("test-key", "test-secret"),
            request_interval_seconds=0.0,
        ),
        output_dir=tmp_path,
        batch_size=100,
        lookback_calendar_days=45,
        reference_sessions=2,
        minimum_reference_sessions=2,
    )


def _bar(timestamp: str, price: float, volume: int) -> dict[str, object]:
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
