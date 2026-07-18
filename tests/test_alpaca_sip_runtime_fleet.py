from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path

import httpx2
import pytest

from tests.alpaca_sip_runtime_fleet_fixtures import (
    NEW_YORK,
    NOW,
    SYMBOLS,
    decision,
    feature_requests,
    fleet,
    opportunity,
    wire_bars,
)
from trading_agent.us_feature_evidence_models import (
    UsFeatureGateBlocked,
    UsFeatureGateBlockedReason,
    UsFeatureGateReady,
)
from trading_agent.us_feature_evidence_projection import project_us_opportunity_with_feature_evidence
from trading_agent.us_market_data_fleet import (
    RuntimeFleetStatus,
    RuntimeOwnerStatus,
    UsMarketDataFleetError,
)


def test_two_candidates_receive_independent_runtime_owners_and_ready_bindings(
    tmp_path: Path,
) -> None:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        symbol = request.url.params["symbols"]
        return httpx2.Response(
            200,
            json={"bars": {symbol: wire_bars(symbol, 35)}, "next_page_token": None},
        )

    runtime_fleet = fleet(tmp_path, respond)
    result = runtime_fleet.run_cycle(decision(), feature_requests())
    gate = project_us_opportunity_with_feature_evidence(
        opportunity(),
        result.bindings,
        evaluated_at=NOW,
    )

    assert result.status is RuntimeFleetStatus.READY
    assert tuple(item.status for item in result.outcomes) == (
        RuntimeOwnerStatus.READY,
        RuntimeOwnerStatus.READY,
    )
    assert tuple(binding.symbol for binding in result.bindings) == SYMBOLS
    assert type(gate) is UsFeatureGateReady
    assert runtime_fleet.active_instrument_ids == (
        "alpaca:asset-aaa",
        "alpaca:asset-bbb",
    )
    assert len(requests) == 2
    assert all(request.method == "GET" for request in requests)
    assert all(request.url.host == "data.alpaca.markets" for request in requests)
    assert all(request.url.path == "/v2/stocks/bars" for request in requests)
    owner_dirs = tuple(path for path in (tmp_path / "owners").iterdir() if path.is_dir())
    assert len(owner_dirs) == 2
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o700 for path in owner_dirs)
    assert all(stat.S_IMODE((path / "runtime.sqlite3").stat().st_mode) == 0o600 for path in owner_dirs)
    assert all(stat.S_IMODE((path / "evidence.sqlite3").stat().st_mode) == 0o600 for path in owner_dirs)
    assert len(tuple((tmp_path / "canonical").rglob("events.parquet"))) == 2


def test_gap_in_one_owner_keeps_other_ready_but_degrades_fleet(tmp_path: Path) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        symbol = request.url.params["symbols"]
        bars = wire_bars(symbol, 35)
        if symbol == "BBB":
            bars = (*bars[:1], *bars[2:])
        return httpx2.Response(
            200,
            json={"bars": {symbol: bars}, "next_page_token": None},
        )

    result = fleet(tmp_path, respond).run_cycle(decision(), feature_requests())
    gate = project_us_opportunity_with_feature_evidence(
        opportunity(),
        result.bindings,
        evaluated_at=NOW,
    )

    assert result.status is RuntimeFleetStatus.DEGRADED
    assert tuple(item.status for item in result.outcomes) == (
        RuntimeOwnerStatus.READY,
        RuntimeOwnerStatus.BLOCKED,
    )
    assert tuple(binding.symbol for binding in result.bindings) == ("AAA",)
    assert type(gate) is UsFeatureGateBlocked
    assert gate.reason is UsFeatureGateBlockedReason.MISSING_EVIDENCE


def test_provider_failure_isolated_to_exact_owner(tmp_path: Path) -> None:
    requests: list[str] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        symbol = request.url.params["symbols"]
        requests.append(symbol)
        if symbol == "BBB":
            return httpx2.Response(503, content=b"provider unavailable")
        return httpx2.Response(
            200,
            json={"bars": {symbol: wire_bars(symbol, 35)}, "next_page_token": None},
        )

    result = fleet(tmp_path, respond).run_cycle(decision(), feature_requests())

    assert requests == ["AAA", "BBB"]
    assert result.status is RuntimeFleetStatus.DEGRADED
    assert tuple(item.status for item in result.outcomes) == (
        RuntimeOwnerStatus.READY,
        RuntimeOwnerStatus.FAILED,
    )
    assert tuple(binding.symbol for binding in result.bindings) == ("AAA",)


def test_restarted_fleet_reuses_each_owner_checkpoint(tmp_path: Path) -> None:
    first_now = dt.datetime(2026, 7, 17, 9, 50, 30, tzinfo=NEW_YORK)

    def first_response(request: httpx2.Request) -> httpx2.Response:
        symbol = request.url.params["symbols"]
        return httpx2.Response(
            200,
            json={"bars": {symbol: wire_bars(symbol, 20)}, "next_page_token": None},
        )

    first = fleet(tmp_path, first_response, now=first_now).run_cycle(
        decision(first_now),
        feature_requests(),
    )

    def second_response(request: httpx2.Request) -> httpx2.Response:
        symbol = request.url.params["symbols"]
        return httpx2.Response(
            200,
            json={"bars": {symbol: wire_bars(symbol, 35)}, "next_page_token": None},
        )

    second = fleet(tmp_path, second_response).run_cycle(
        decision(),
        feature_requests(),
    )

    assert first.status is RuntimeFleetStatus.DEGRADED
    assert second.status is RuntimeFleetStatus.READY
    assert tuple(
        outcome.runtime_result.inserted_receipt_count
        for outcome in first.outcomes
        if outcome.runtime_result is not None
    ) == (20, 20)
    assert tuple(
        outcome.runtime_result.inserted_receipt_count
        for outcome in second.outcomes
        if outcome.runtime_result is not None
    ) == (15, 15)


def test_request_coverage_mismatch_blocks_before_owner_or_http(tmp_path: Path) -> None:
    requests: list[httpx2.Request] = []
    runtime_fleet = fleet(
        tmp_path,
        lambda request: requests.append(request) or httpx2.Response(500),
    )

    with pytest.raises(UsMarketDataFleetError, match="market data fleet input is invalid"):
        _ = runtime_fleet.run_cycle(decision(), feature_requests()[:1])

    assert requests == []
    assert runtime_fleet.active_instrument_ids == ()


def test_symlinked_owner_root_fails_before_http(tmp_path: Path) -> None:
    target = tmp_path / "owner-target"
    target.mkdir(mode=0o700)
    (tmp_path / "owners").symlink_to(target, target_is_directory=True)
    requests: list[httpx2.Request] = []
    runtime_fleet = fleet(
        tmp_path,
        lambda request: requests.append(request) or httpx2.Response(500),
    )

    result = runtime_fleet.run_cycle(decision(), feature_requests())

    assert result.status is RuntimeFleetStatus.DEGRADED
    assert tuple(item.status for item in result.outcomes) == (
        RuntimeOwnerStatus.FAILED,
        RuntimeOwnerStatus.FAILED,
    )
    assert requests == []
    assert tuple(target.iterdir()) == ()
