from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
import pytest

from trading_agent.alpaca_http import ALPACA_DATA_URL, AlpacaCredentials
from trading_agent.alpaca_sip_historical_profile import (
    AlpacaSipHistoricalProfileCollector,
    AlpacaSipHistoricalProfileError,
)
from trading_agent.alpaca_sip_runtime_evidence import AlpacaSipRuntimeEvidenceProjector
from trading_agent.alpaca_sip_runtime_evidence_store import AlpacaSipRuntimeEvidenceStore
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient

_NY = ZoneInfo("America/New_York")
_TARGET = dt.date(2026, 7, 17)
_INSTRUMENT_ID = "alpaca:asset-acme"
_SYMBOL = "ACME"
_RECEIVED = dt.datetime(2026, 7, 17, 8, tzinfo=_NY)


def test_collects_twenty_raw_first_sessions_then_replays_without_http(tmp_path: Path) -> None:
    calls: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return _response(request, complete=True)

    first = _collector(tmp_path, respond).collect(
        _INSTRUMENT_ID,
        _SYMBOL,
        _TARGET,
        through_minute=35,
    )
    first_call_count = len(calls)
    second = _collector(
        tmp_path,
        lambda _request: pytest.fail("durable replay must not open HTTP"),
    ).collect(_INSTRUMENT_ID, _SYMBOL, _TARGET, through_minute=35)

    assert first == second
    assert first_call_count == 20
    assert len(first.source_identities) == 20
    assert len({item.identity_sha256 for item in first.source_identities}) == 20
    assert first.expected_cumulative_volume == Decimal(35_000)
    assert all(request.method == "GET" for request in calls)
    assert all(request.url.host == "data.alpaca.markets" for request in calls)
    assert all(request.url.path == "/v2/stocks/bars" for request in calls)
    assert len(tuple((tmp_path / "canonical").rglob("events.parquet"))) == 20


def test_incomplete_session_is_persisted_but_profile_is_blocked(tmp_path: Path) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        incomplete = request.url.params["asof"] == "2026-07-16"
        return _response(request, complete=not incomplete)

    with pytest.raises(AlpacaSipHistoricalProfileError, match="blocked"):
        _collector(tmp_path, respond).collect(
            _INSTRUMENT_ID,
            _SYMBOL,
            _TARGET,
            through_minute=35,
        )

    assert AlpacaSipRuntimeEvidenceStore(tmp_path / "evidence.sqlite3").page_count() > 0


def test_tampered_canonical_session_blocks_durable_replay_without_http(
    tmp_path: Path,
) -> None:
    collector = _collector(tmp_path, lambda request: _response(request, complete=True))
    _ = collector.collect(_INSTRUMENT_ID, _SYMBOL, _TARGET, through_minute=35)
    parquet = next((tmp_path / "canonical").rglob("events.parquet"))
    parquet.write_bytes(parquet.read_bytes() + b"tampered")

    with pytest.raises(AlpacaSipHistoricalProfileError, match="blocked"):
        _collector(
            tmp_path,
            lambda _request: pytest.fail("tampered replay must not open HTTP"),
        ).collect(_INSTRUMENT_ID, _SYMBOL, _TARGET, through_minute=35)


def _collector(
    root: Path,
    responder: Callable[[httpx2.Request], httpx2.Response],
) -> AlpacaSipHistoricalProfileCollector:
    client = httpx2.Client(
        base_url=ALPACA_DATA_URL,
        transport=httpx2.MockTransport(responder),
        follow_redirects=False,
    )
    store = AlpacaSipRuntimeEvidenceStore(root / "evidence.sqlite3")
    page_client = AlpacaSipMinutePageClient(
        client,
        AlpacaCredentials("fixture-key", "fixture-secret"),
        clock=lambda: _RECEIVED,
    )
    projector = AlpacaSipRuntimeEvidenceProjector(store, root / "canonical")
    return AlpacaSipHistoricalProfileCollector(page_client, store, projector)


def _response(request: httpx2.Request, *, complete: bool) -> httpx2.Response:
    opened = dt.datetime.fromisoformat(request.url.params["start"]).astimezone(_NY)
    closed = dt.datetime.fromisoformat(request.url.params["end"]).astimezone(_NY)
    count = int((closed - opened) / dt.timedelta(minutes=1)) + 1
    if not complete:
        count -= 1
    bars = tuple(
        {
            "t": (opened + dt.timedelta(minutes=index)).isoformat(),
            "o": 100.0,
            "h": 101.0,
            "l": 99.0,
            "c": 100.0,
            "v": 1000,
            "n": 10,
            "vw": 100.0,
        }
        for index in range(count)
    )
    return httpx2.Response(
        200,
        json={"bars": {_SYMBOL: bars}, "next_page_token": None},
    )
