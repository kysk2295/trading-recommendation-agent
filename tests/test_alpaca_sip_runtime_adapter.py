from __future__ import annotations

import datetime as dt
import json
import sqlite3
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx2
import pytest

from trading_agent.alpaca_http import ALPACA_DATA_URL, AlpacaCredentials
from trading_agent.alpaca_sip_runtime_adapter import AlpacaSipRuntimeAdapter
from trading_agent.alpaca_sip_runtime_evidence import (
    AlpacaSipRuntimeEvidenceProjector,
    AlpacaSipRuntimeEvidenceStore,
)
from trading_agent.alpaca_sip_runtime_http import AlpacaSipMinutePageClient
from trading_agent.alpaca_sip_runtime_models import (
    AlpacaSipMinutePageRequest,
    AlpacaSipRuntimeContext,
    AlpacaSipRuntimeError,
)
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.intraday_feature_kernel import FeatureSnapshotStatus
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_dynamic_subscription_policy import (
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionPolicyConfig,
    build_subscription_policy_decision,
)
from trading_agent.us_market_data_runtime_models import MarketDataRuntimeStatus, RuntimeFeatureRequest
from trading_agent.us_market_data_runtime_store import MarketDataRuntimeStore
from trading_agent.us_market_data_supervisor import UsMarketDataSupervisor
from trading_agent.us_subscription_models import (
    DesiredMarketDataSubscription,
    SubscriptionChannel,
)

_NY = ZoneInfo("America/New_York")
_SESSION_DATE = dt.date(2026, 7, 17)
_NOW = dt.datetime(2026, 7, 17, 10, 5, 30, tzinfo=_NY)
_SOURCE_ID = "alpaca.sip.us_equities"
_INSTRUMENT_ID = "us-eq-fixture-acme"
_SYMBOL = "ACME"
_RAW_MARKER = "exact-wire-page-marker"


def test_paginated_sip_wire_response_reaches_ready_supervisor_with_verified_identity(
    tmp_path: Path,
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        page_token = request.url.params.get("page_token")
        if page_token is None:
            bars = _wire_bars(0, 20)
            next_page_token = "page-2"
        else:
            assert page_token == "page-2"
            bars = _wire_bars(20, 15)
            next_page_token = None
        return httpx2.Response(
            200,
            content=json.dumps(
                {
                    "bars": {_SYMBOL: bars},
                    "next_page_token": next_page_token,
                    "wire_marker": _RAW_MARKER,
                }
            ).encode(),
            headers={"content-type": "application/json"},
        )

    adapter, evidence = _adapter(tmp_path, _NOW, respond)
    runtime = MarketDataRuntimeStore(tmp_path / "runtime.sqlite3")
    result = UsMarketDataSupervisor(adapter, runtime, clock=lambda: _NOW).run_cycle(
        _decision(_NOW),
        _feature_requests(),
    )

    assert result.status is MarketDataRuntimeStatus.READY
    assert result.inserted_receipt_count == 35
    assert result.last_sequence == 35
    assert result.feature_snapshots[0].status is FeatureSnapshotStatus.READY
    assert result.feature_snapshots[0].identity.scope == "us_equities.day_trading.runtime_features"
    assert result.feature_snapshots[0].identity.dataset_id
    assert evidence.page_count() == 2
    assert evidence.projection_count() == 1
    assert len(tuple((tmp_path / "canonical").rglob("events.parquet"))) == 1
    with sqlite3.connect(tmp_path / "alpaca-evidence.sqlite3") as connection:
        raw_pages: list[tuple[bytes]] = connection.execute(
            "SELECT raw_response FROM alpaca_sip_raw_pages ORDER BY generation"
        ).fetchall()
    assert len(raw_pages) == 2
    assert all(_RAW_MARKER.encode() in row[0] for row in raw_pages)
    assert len(requests) == 2
    assert all(request.method == "GET" for request in requests)
    assert all(request.url.host == "data.alpaca.markets" for request in requests)
    assert all(request.url.path == "/v2/stocks/bars" for request in requests)
    assert requests[0].url.params["symbols"] == _SYMBOL
    assert requests[0].url.params["timeframe"] == "1Min"
    assert requests[0].url.params["feed"] == "sip"
    assert requests[0].url.params["adjustment"] == "raw"
    assert requests[0].url.params["sort"] == "asc"
    assert requests[0].url.params.get("page_token") is None
    assert requests[1].url.params["page_token"] == "page-2"


def test_restart_uses_runtime_offset_and_persisted_bars(tmp_path: Path) -> None:
    first_now = dt.datetime(2026, 7, 17, 9, 50, 30, tzinfo=_NY)
    first_adapter, _ = _adapter(
        tmp_path,
        first_now,
        _single_page_responder(_wire_bars(0, 20)),
    )
    runtime = MarketDataRuntimeStore(tmp_path / "runtime.sqlite3")
    first = UsMarketDataSupervisor(first_adapter, runtime, clock=lambda: first_now).run_cycle(
        _decision(first_now),
        _feature_requests(),
    )

    second_adapter, evidence = _adapter(
        tmp_path,
        _NOW,
        _single_page_responder(_wire_bars(0, 35)),
    )
    second = UsMarketDataSupervisor(second_adapter, runtime, clock=lambda: _NOW).run_cycle(
        _decision(_NOW),
        _feature_requests(),
    )

    assert first.last_sequence == 20
    assert first.feature_snapshots[0].status is FeatureSnapshotStatus.BLOCKED_INSUFFICIENT_HISTORY
    assert second.inserted_receipt_count == 15
    assert second.last_sequence == 35
    assert second.feature_snapshots[0].status is FeatureSnapshotStatus.READY
    assert evidence.page_count() == 2
    assert evidence.projection_count() == 2


def test_exact_same_minute_retry_reuses_verified_projection(tmp_path: Path) -> None:
    runtime = MarketDataRuntimeStore(tmp_path / "runtime.sqlite3")
    first_adapter, _ = _adapter(
        tmp_path,
        _NOW,
        _single_page_responder(_wire_bars(0, 35)),
    )
    first = UsMarketDataSupervisor(first_adapter, runtime, clock=lambda: _NOW).run_cycle(
        _decision(_NOW),
        _feature_requests(),
    )

    retry_adapter, evidence = _adapter(
        tmp_path,
        _NOW,
        _single_page_responder(_wire_bars(0, 35)),
    )
    retry = UsMarketDataSupervisor(retry_adapter, runtime, clock=lambda: _NOW).run_cycle(
        _decision(_NOW),
        _feature_requests(),
    )

    assert first.status is MarketDataRuntimeStatus.READY
    assert retry.status is MarketDataRuntimeStatus.NO_NEW_DATA
    assert retry.inserted_receipt_count == 0
    assert evidence.page_count() == 1
    assert evidence.projection_count() == 1


def test_missing_provider_minute_is_persisted_then_blocks_sequence_gap(tmp_path: Path) -> None:
    now = dt.datetime(2026, 7, 17, 9, 33, 30, tzinfo=_NY)
    adapter, evidence = _adapter(
        tmp_path,
        now,
        _single_page_responder((_wire_bar(0), _wire_bar(2))),
    )
    runtime = MarketDataRuntimeStore(tmp_path / "runtime.sqlite3")

    result = UsMarketDataSupervisor(adapter, runtime, clock=lambda: now).run_cycle(
        _decision(now),
        _feature_requests(),
    )

    assert result.status is MarketDataRuntimeStatus.BLOCKED_SEQUENCE_GAP
    assert result.last_sequence == 3
    assert runtime.receipt_count(_SOURCE_ID) == 2
    assert evidence.page_count() == 1
    assert evidence.projection_count() == 1


@pytest.mark.parametrize("fault", ("closed", "multiple"))
def test_closed_session_or_multiple_symbols_is_blocked_before_http(
    tmp_path: Path,
    fault: str,
) -> None:
    requests: list[httpx2.Request] = []
    now = dt.datetime(2026, 7, 18, 10, 0, tzinfo=_NY) if fault == "closed" else _NOW

    def unexpected(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        raise AssertionError(request.url)

    adapter, evidence = _adapter(tmp_path, now, unexpected)
    desired = _decision(_NOW).desired
    if fault == "multiple":
        desired = (
            *desired,
            DesiredMarketDataSubscription(
                instrument_id="us-eq-fixture-other",
                symbol="OTHER",
                channels=(SubscriptionChannel.QUOTE, SubscriptionChannel.TRADE),
            ),
        )

    with pytest.raises(AlpacaSipRuntimeError, match="alpaca SIP runtime input is invalid"):
        _ = adapter.read_batch(desired, None)

    assert requests == []
    assert evidence.page_count() == 0


@pytest.mark.parametrize("fault", ("base_url", "redirect"))
def test_noncanonical_base_url_or_redirect_fails_closed(
    fault: str,
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(302, headers={"location": "https://example.com/capture"})

    base_url = "https://example.com" if fault == "base_url" else ALPACA_DATA_URL
    with httpx2.Client(
        base_url=base_url,
        transport=httpx2.MockTransport(respond),
        follow_redirects=False,
    ) as client:
        page_client = AlpacaSipMinutePageClient(
            client,
            AlpacaCredentials("test-key", "test-secret"),
            clock=lambda: _NOW,
        )
        with pytest.raises(AlpacaSipRuntimeError, match="alpaca SIP runtime input is invalid"):
            _ = page_client.fetch_page(_page_request(_NOW))

    assert len(requests) == (0 if fault == "base_url" else 1)
    if requests:
        assert requests[0].url.host == "data.alpaca.markets"


def _adapter(
    tmp_path: Path,
    now: dt.datetime,
    responder: Callable[[httpx2.Request], httpx2.Response],
) -> tuple[AlpacaSipRuntimeAdapter, AlpacaSipRuntimeEvidenceStore]:
    client = httpx2.Client(
        base_url=ALPACA_DATA_URL,
        transport=httpx2.MockTransport(responder),
        follow_redirects=False,
    )
    page_client = AlpacaSipMinutePageClient(
        client,
        AlpacaCredentials("test-key", "test-secret"),
        clock=lambda: now,
    )
    evidence = AlpacaSipRuntimeEvidenceStore(tmp_path / "alpaca-evidence.sqlite3")
    projector = AlpacaSipRuntimeEvidenceProjector(evidence, tmp_path / "canonical")
    adapter = AlpacaSipRuntimeAdapter(
        page_client,
        projector,
        AlpacaSipRuntimeContext(session_date=_SESSION_DATE, clock=lambda: now),
    )
    return adapter, evidence


def _single_page_responder(
    bars: tuple[dict[str, float | int | str | None], ...],
) -> Callable[[httpx2.Request], httpx2.Response]:
    def respond(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={"bars": {_SYMBOL: bars}, "next_page_token": None},
        )

    return respond


def _wire_bars(start: int, count: int) -> tuple[dict[str, float | int | str | None], ...]:
    return tuple(_wire_bar(index) for index in range(start, start + count))


def _wire_bar(index: int) -> dict[str, float | int | str | None]:
    timestamp = dt.datetime(2026, 7, 17, 13, 30, tzinfo=dt.UTC) + dt.timedelta(minutes=index)
    close = 100.0 + index / 10
    return {
        "t": timestamp.isoformat().replace("+00:00", "Z"),
        "o": close,
        "h": close + 0.5,
        "l": close - 0.5,
        "c": close,
        "v": 100 + index,
        "n": 10 + index,
        "vw": close,
    }


def _identity() -> ResearchInputIdentity:
    replay = CanonicalDatasetReplay(
        dataset_id="ds_alpaca_scanner_fixture",
        event_count=1,
        canonical_event_content_sha256="a" * 64,
        parquet_sha256="c" * 64,
        raw_manifest_id="raw_alpaca_scanner_fixture",
        raw_manifest_content_sha256="b" * 64,
    )
    return ResearchInputIdentity.from_verified_replay("us_equities.scanner", replay)


def _decision(now: dt.datetime):
    return build_subscription_policy_decision(
        BroadScannerSnapshot(
            identity=_identity(),
            observed_at=now - dt.timedelta(seconds=1),
            candidates=(
                BroadScannerCandidate(
                    instrument_id=_INSTRUMENT_ID,
                    symbol=_SYMBOL,
                    priority_score=Decimal("1"),
                    source_rank=1,
                ),
            ),
        ),
        evaluated_at=now,
        active=(),
        cooldowns=(),
        config=SubscriptionPolicyConfig(
            capacity=1,
            max_candidate_age=dt.timedelta(seconds=30),
            minimum_residency=dt.timedelta(minutes=2),
            eviction_cooldown=dt.timedelta(minutes=5),
        ),
    )


def _feature_requests() -> tuple[RuntimeFeatureRequest, ...]:
    return (
        RuntimeFeatureRequest(
            instrument_id=_INSTRUMENT_ID,
            expected_cumulative_volume=Decimal("4000"),
        ),
    )


def _page_request(now: dt.datetime) -> AlpacaSipMinutePageRequest:
    return AlpacaSipMinutePageRequest(
        session_date=_SESSION_DATE,
        symbol=_SYMBOL,
        start_at=dt.datetime(2026, 7, 17, 9, 30, tzinfo=_NY),
        end_at=now.replace(second=0, microsecond=0) - dt.timedelta(microseconds=1),
    )
