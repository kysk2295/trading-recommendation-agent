from __future__ import annotations

import datetime as dt
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_feature_bridge as trade_fixtures
from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_quote_feature_bridge as quote_fixtures
from tests.alpaca_sip_dynamic_reconnect_fixtures import ConnectorQueue, FakeConnection, FixtureClock
from tests.test_alpaca_sip_live_actionability import (
    _CAPTURE_AT,
    _EPOCH,
    _connection,
    _dependencies,
    _request,
)
from trading_agent.alpaca_sip_live_actionability import AlpacaSipLiveActionabilityRequest
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    build_alpaca_sip_quote_actionability_manifest,
    write_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore
from trading_agent.trade_signal_publication import TradeSignalPublication
from trading_agent.us_runtime_live_actionability_dispatch import (
    UsRuntimeLiveActionabilityDispatchError,
    UsRuntimeLiveActionabilityDispatchRequest,
    dispatch_us_runtime_live_actionability,
)


def test_dispatches_only_current_cycle_manifest_to_private_receipt(tmp_path: Path) -> None:
    source = _request(tmp_path / "state")
    manifest_root = tmp_path / "manifests"
    _write_manifest(manifest_root, source)
    queue = ConnectorQueue([_connection()])

    result = dispatch_us_runtime_live_actionability(
        _dispatch_request(tmp_path, manifest_root, source),
        _dependencies(queue, FixtureClock(_CAPTURE_AT), (_EPOCH,)),
    )

    receipt = tmp_path / "live-receipts" / f"{_digest(source)}.sqlite3"
    assert (result.selected_count, result.created_count, result.replay_count) == (1, 1, 0)
    assert queue.calls == 1
    assert receipt.is_file()
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert stat.S_IMODE(receipt.parent.stat().st_mode) == 0o700
    assert len(AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3").records()) == 1


def test_exact_restart_replays_without_websocket_connection(tmp_path: Path) -> None:
    source = _request(tmp_path / "state")
    manifest_root = tmp_path / "manifests"
    _write_manifest(manifest_root, source)
    first_queue = ConnectorQueue([_connection()])
    request = _dispatch_request(tmp_path, manifest_root, source)
    _ = dispatch_us_runtime_live_actionability(
        request,
        _dependencies(first_queue, FixtureClock(_CAPTURE_AT), (_EPOCH,)),
    )
    replay_queue = ConnectorQueue([_connection()])

    result = dispatch_us_runtime_live_actionability(
        request,
        _dependencies(
            replay_queue,
            FixtureClock(_CAPTURE_AT + dt.timedelta(seconds=1)),
            ("2" * 32,),
        ),
    )

    assert (result.selected_count, result.created_count, result.replay_count) == (1, 0, 1)
    assert replay_queue.calls == 0


def test_stale_manifest_is_ignored_without_creating_receipt_root(tmp_path: Path) -> None:
    source = _request(tmp_path / "state")
    manifest_root = tmp_path / "manifests"
    _write_manifest(manifest_root, source)
    queue = ConnectorQueue([_connection()])
    request = _dispatch_request(tmp_path, manifest_root, source)
    stale_cycle = source.manifest.snapshot.observed_at + dt.timedelta(seconds=1)

    result = dispatch_us_runtime_live_actionability(
        UsRuntimeLiveActionabilityDispatchRequest(
            request.manifest_root,
            stale_cycle,
            request.credentials,
            request.plan_store,
            request.policy_state_store,
            request.receipt_root,
            request.actionability_store,
            request.config,
        ),
        _dependencies(queue, FixtureClock(stale_cycle), (_EPOCH,)),
    )

    assert (result.selected_count, result.created_count, result.replay_count) == (0, 0, 0)
    assert queue.calls == 0
    assert not (tmp_path / "live-receipts").exists()


def test_public_manifest_blocks_batch_before_connection_or_output(tmp_path: Path) -> None:
    source = _request(tmp_path / "state")
    manifest_root = tmp_path / "manifests"
    manifest_path = _write_manifest(manifest_root, source)
    manifest_path.chmod(0o644)
    queue = ConnectorQueue([_connection()])

    with pytest.raises(UsRuntimeLiveActionabilityDispatchError):
        _ = dispatch_us_runtime_live_actionability(
            _dispatch_request(tmp_path, manifest_root, source),
            _dependencies(queue, FixtureClock(_CAPTURE_AT), (_EPOCH,)),
        )

    assert queue.calls == 0
    assert not (tmp_path / "live-receipts").exists()
    assert not (tmp_path / "actionability.sqlite3").exists()


def test_next_minute_same_base_terminal_replays_before_connector(tmp_path: Path) -> None:
    source = _long_lived_request(tmp_path / "state")
    manifest_root = tmp_path / "manifests"
    _write_manifest(manifest_root, source)
    first_queue = ConnectorQueue([_connection()])
    first_request = _dispatch_request(tmp_path, manifest_root, source)
    _ = dispatch_us_runtime_live_actionability(
        first_request,
        _dependencies(first_queue, FixtureClock(_CAPTURE_AT), (_EPOCH,)),
    )
    next_at = source.manifest.snapshot.observed_at + dt.timedelta(minutes=1)
    next_manifest = build_alpaca_sip_quote_actionability_manifest(
        source.manifest.base_publication,
        replace(source.manifest.snapshot, observed_at=next_at),
        source.manifest.plan,
        scan_started_at=source.manifest.scan_started_at,
    )
    next_path = manifest_root / f"{next_manifest.manifest_id.rpartition(':')[2]}.json"
    assert write_alpaca_sip_quote_actionability_manifest(next_path, next_manifest)
    replay_queue = ConnectorQueue([_connection()])

    result = dispatch_us_runtime_live_actionability(
        UsRuntimeLiveActionabilityDispatchRequest(
            first_request.manifest_root,
            next_at,
            first_request.credentials,
            first_request.plan_store,
            first_request.policy_state_store,
            first_request.receipt_root,
            first_request.actionability_store,
            first_request.config,
        ),
        _dependencies(replay_queue, FixtureClock(next_at + dt.timedelta(seconds=1)), ("2" * 32,)),
    )

    assert (result.selected_count, result.created_count, result.replay_count) == (1, 0, 1)
    assert replay_queue.calls == 0
    assert not (tmp_path / "live-receipts" / f"{next_path.stem}.sqlite3").exists()


def test_next_minute_new_base_terminal_connects(tmp_path: Path) -> None:
    source = _long_lived_request(tmp_path / "state")
    manifest_root = tmp_path / "manifests"
    _write_manifest(manifest_root, source)
    first_request = _dispatch_request(tmp_path, manifest_root, source)
    _ = dispatch_us_runtime_live_actionability(
        first_request,
        _dependencies(ConnectorQueue([_connection()]), FixtureClock(_CAPTURE_AT), (_EPOCH,)),
    )
    next_at = source.manifest.snapshot.observed_at + dt.timedelta(minutes=1)
    payload = source.manifest.base_publication.model_dump(mode="python")
    payload["signal"]["signal_id"] = "next-minute-signal"
    next_base = TradeSignalPublication.model_validate(payload)
    next_manifest = build_alpaca_sip_quote_actionability_manifest(
        next_base,
        replace(source.manifest.snapshot, observed_at=next_at),
        source.manifest.plan,
        scan_started_at=source.manifest.scan_started_at,
    )
    next_path = manifest_root / f"{next_manifest.manifest_id.rpartition(':')[2]}.json"
    assert write_alpaca_sip_quote_actionability_manifest(next_path, next_manifest)
    next_queue = ConnectorQueue([_connection_at(next_at)])

    result = dispatch_us_runtime_live_actionability(
        replace(first_request, evaluated_at=next_at),
        _dependencies(next_queue, FixtureClock(next_at + dt.timedelta(seconds=1)), ("2" * 32,)),
    )

    assert (result.selected_count, result.created_count, result.replay_count) == (1, 1, 0)
    assert next_queue.calls == 1
    assert (tmp_path / "live-receipts" / f"{next_path.stem}.sqlite3").is_file()


def _connection_at(observed_at: dt.datetime) -> FakeConnection:
    timestamp = observed_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    quote = quote_fixtures._quote(100.01, 100.03, bid_size=300, ask_size=100)
    quote["t"] = timestamp
    trade = trade_fixtures._trade(101, 100.02)
    trade["t"] = timestamp
    return FakeConnection(
        [
            dynamic_fixtures._connected(),
            dynamic_fixtures._authenticated(),
            dynamic_fixtures._ack(),
            dynamic_fixtures._frame(quote, trade),
        ]
    )


def _long_lived_request(tmp_path: Path) -> AlpacaSipLiveActionabilityRequest:
    source = _request(tmp_path)
    payload = source.manifest.base_publication.model_dump(mode="python")
    payload["signal"]["valid_until"] = source.manifest.snapshot.observed_at + dt.timedelta(minutes=2)
    base = TradeSignalPublication.model_validate(payload)
    manifest = build_alpaca_sip_quote_actionability_manifest(
        base,
        source.manifest.snapshot,
        source.manifest.plan,
        scan_started_at=source.manifest.scan_started_at,
    )
    return replace(source, manifest=manifest)


def _dispatch_request(
    tmp_path: Path,
    manifest_root: Path,
    source: AlpacaSipLiveActionabilityRequest,
) -> UsRuntimeLiveActionabilityDispatchRequest:
    return UsRuntimeLiveActionabilityDispatchRequest(
        manifest_root,
        source.manifest.snapshot.observed_at,
        source.credentials,
        source.stores.plan.path,
        source.stores.policy.path,
        tmp_path / "live-receipts",
        tmp_path / "actionability.sqlite3",
        source.config,
    )


def _write_manifest(
    manifest_root: Path,
    source: AlpacaSipLiveActionabilityRequest,
) -> Path:
    path = manifest_root / f"{_digest(source)}.json"
    assert write_alpaca_sip_quote_actionability_manifest(path, source.manifest)
    return path


def _digest(source: AlpacaSipLiveActionabilityRequest) -> str:
    return source.manifest.manifest_id.rpartition(":")[2]
