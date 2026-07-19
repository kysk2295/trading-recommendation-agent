from __future__ import annotations

import datetime as dt
import stat
from pathlib import Path

import pytest

from tests.alpaca_sip_dynamic_reconnect_fixtures import ConnectorQueue, FixtureClock
from tests.test_alpaca_sip_live_actionability import (
    _CAPTURE_AT,
    _EPOCH,
    _connection,
    _dependencies,
    _request,
)
from trading_agent.alpaca_sip_live_actionability import AlpacaSipLiveActionabilityRequest
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    write_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore
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
