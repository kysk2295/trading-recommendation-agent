from __future__ import annotations

import datetime as dt
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx2
import pytest

from tests.test_us_news_catalyst_feature_observations import SETUP_AT, SYMBOLS, _cohort
from trading_agent.alpaca_http import ALPACA_DATA_URL, AlpacaCredentials
from trading_agent.alpaca_security_master_models import (
    AlpacaSecurityMasterSnapshot,
    build_alpaca_security_master_snapshot,
)
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.security_master_models import (
    AssetClass,
    DataMarketDomain,
    InstrumentAlias,
    InstrumentAliasType,
    InstrumentId,
)
from trading_agent.us_news_catalyst_cohort_collection import (
    InvalidUsNewsCatalystCohortCollectionError,
    UsNewsCatalystCohortCollectionPaths,
    UsNewsCatalystCohortCollector,
)
from trading_agent.us_news_catalyst_feature_artifact import feature_artifacts_in


def test_complete_cohort_collects_all_features_and_replays_without_http(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _bars_response(request)

    collector = _collector(tmp_path, respond)
    cohort = _cohort(tmp_path)

    first = collector.collect(cohort, _security_master(), evaluated_at=SETUP_AT)
    request_count = len(requests)
    second = collector.collect(
        cohort,
        _security_master(),
        evaluated_at=SETUP_AT + dt.timedelta(seconds=30),
    )

    assert second.receipt == first.receipt
    assert second.created is False
    assert request_count == 84
    assert len(requests) == request_count
    assert tuple(item.symbol for item in first.receipt.content.features) == SYMBOLS
    assert len(feature_artifacts_in(tmp_path / "features")) == 4
    assert all(request.method == "GET" for request in requests)
    assert all(request.url.host == "data.alpaca.markets" for request in requests)
    assert all(request.url.path == "/v2/stocks/bars" for request in requests)
    assert stat.S_IMODE(first.receipt_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(first.plan_path.stat().st_mode) == 0o600
    assert all(
        stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o700
        for name in ("profiles", "runtime", "canonical", "features", "receipts")
    )


def test_missing_security_master_symbol_blocks_before_http(tmp_path: Path) -> None:
    called = False

    def respond(_request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(500)

    collector = _collector(tmp_path, respond)
    cohort = _cohort(tmp_path)

    with pytest.raises(InvalidUsNewsCatalystCohortCollectionError):
        _ = collector.collect(
            cohort,
            _security_master(symbols=SYMBOLS[:-1]),
            evaluated_at=SETUP_AT,
        )

    assert called is False
    assert feature_artifacts_in(tmp_path / "features") == ()


def test_stale_collection_time_blocks_before_http(tmp_path: Path) -> None:
    called = False

    def respond(_request: httpx2.Request) -> httpx2.Response:
        nonlocal called
        called = True
        return httpx2.Response(500)

    collector = _collector(tmp_path, respond)

    with pytest.raises(InvalidUsNewsCatalystCohortCollectionError):
        _ = collector.collect(
            _cohort(tmp_path),
            _security_master(),
            evaluated_at=SETUP_AT + dt.timedelta(minutes=1, microseconds=1),
        )

    assert called is False


def test_partial_current_collection_restarts_from_raw_receipts(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []
    failed = False

    def respond(request: httpx2.Request) -> httpx2.Response:
        nonlocal failed
        requests.append(request)
        current_tsla = (
            request.url.params["asof"] == "2026-07-21"
            and request.url.params["symbols"] == "TSLA"
        )
        if current_tsla and not failed:
            failed = True
            return httpx2.Response(503)
        return _bars_response(request)

    collector = _collector(tmp_path, respond)
    cohort = _cohort(tmp_path)

    with pytest.raises(InvalidUsNewsCatalystCohortCollectionError):
        _ = collector.collect(cohort, _security_master(), evaluated_at=SETUP_AT)
    recovered = collector.collect(
        cohort,
        _security_master(),
        evaluated_at=SETUP_AT + dt.timedelta(seconds=30),
    )

    assert recovered.created is True
    assert len(requests) == 85
    assert tuple(item.symbol for item in recovered.receipt.content.features) == SYMBOLS


def _collector(
    tmp_path: Path,
    responder: Callable[[httpx2.Request], httpx2.Response],
) -> UsNewsCatalystCohortCollector:
    client = httpx2.Client(
        base_url=ALPACA_DATA_URL,
        transport=httpx2.MockTransport(responder),
        follow_redirects=False,
    )
    page_client = AlpacaSipMinutePageClient(
        client,
        AlpacaCredentials("fixture-key", "fixture-secret"),
        clock=lambda: SETUP_AT,
    )
    return UsNewsCatalystCohortCollector(
        page_client,
        UsNewsCatalystCohortCollectionPaths(
            plan_root=tmp_path / "plans",
            profile_root=tmp_path / "profiles",
            runtime_root=tmp_path / "runtime",
            canonical_root=tmp_path / "canonical",
            feature_root=tmp_path / "features",
            receipt_root=tmp_path / "receipts",
        ),
    )


def _security_master(
    *,
    symbols: tuple[str, ...] = SYMBOLS,
) -> AlpacaSecurityMasterSnapshot:
    observed_at = dt.datetime(2026, 7, 21, 13, 59, tzinfo=dt.UTC)
    instruments = tuple(
        InstrumentId(
            value=f"alpaca:asset-{symbol.lower()}",
            market_domain=DataMarketDomain.US_EQUITIES,
            asset_class=AssetClass.EQUITY,
            venue="XNAS",
            currency="USD",
            timezone="America/New_York",
            valid_from=observed_at,
        )
        for symbol in symbols
    )
    aliases = tuple(
        InstrumentAlias(
            instrument_id=f"alpaca:asset-{symbol.lower()}",
            namespace="alpaca",
            alias_type=InstrumentAliasType.PROVIDER_SYMBOL,
            value=symbol,
            effective_from=observed_at,
        )
        for symbol in symbols
    )
    return build_alpaca_security_master_snapshot(
        "a" * 64,
        observed_at,
        instruments,
        aliases,
    )


def _bars_response(request: httpx2.Request) -> httpx2.Response:
    opened = dt.datetime.fromisoformat(request.url.params["start"])
    closed = dt.datetime.fromisoformat(request.url.params["end"])
    symbol = request.url.params["symbols"]
    count = int((closed - opened) / dt.timedelta(minutes=1)) + 1
    current = request.url.params["asof"] == "2026-07-21"
    treatment = symbol in {"AAPL", "MSFT"}
    bars = tuple(
        _wire_bar(
            opened + dt.timedelta(minutes=index),
            _WireBarShape(index, count, current, treatment),
        )
        for index in range(count)
    )
    return httpx2.Response(200, json={"bars": {symbol: bars}, "next_page_token": None})


@dataclass(frozen=True, slots=True)
class _WireBarShape:
    index: int
    count: int
    current: bool
    treatment: bool


def _wire_bar(
    timestamp: dt.datetime,
    shape: _WireBarShape,
) -> dict[str, float | int | str]:
    latest = shape.index == shape.count - 1
    close = (
        110.0
        if shape.current and shape.treatment and latest
        else 99.0
        if shape.current and latest
        else 100.0
    )
    high = 111.0 if shape.current and shape.treatment and latest else 101.0
    volume = 2_000 if shape.current and shape.treatment else 1_000
    return {
        "t": timestamp.isoformat(),
        "o": 100.0,
        "h": high,
        "l": 98.0,
        "c": close,
        "v": volume,
        "n": 10,
        "vw": close,
    }
