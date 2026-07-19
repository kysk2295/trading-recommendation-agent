from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests import test_alpaca_sip_dynamic_feature_bridge as trade_fixtures
from tests import test_alpaca_sip_dynamic_projection as dynamic_fixtures
from tests import test_alpaca_sip_dynamic_quote_feature_bridge as quote_fixtures
from tests.alpaca_sip_dynamic_reconnect_fixtures import (
    ConnectorQueue,
    FakeConnection,
    FixtureClock,
    WaitRecorder,
)
from tests.test_alpaca_sip_dynamic_quote_actionability import _SCAN_STARTED_AT, _base
from tests.test_alpaca_sip_dynamic_subscription import _candidates, _decision, _identity
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_dynamic_backoff import AlpacaSipDynamicBackoffConfig
from trading_agent.alpaca_sip_dynamic_plan_store import AlpacaSipDynamicPlanStore
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_live_actionability import (
    AlpacaSipLiveActionabilityConfig,
    AlpacaSipLiveActionabilityDependencies,
    AlpacaSipLiveActionabilityError,
    AlpacaSipLiveActionabilityRequest,
    AlpacaSipLiveActionabilityStores,
    run_alpaca_sip_live_actionability,
)
from trading_agent.alpaca_sip_quote_actionability_manifest import (
    build_alpaca_sip_quote_actionability_manifest,
)
from trading_agent.alpaca_sip_quote_actionability_store import AlpacaSipQuoteActionabilityStore
from trading_agent.us_dynamic_subscription_policy import build_subscription_policy_decision
from trading_agent.us_subscription_models import BroadScannerSnapshot
from trading_agent.us_subscription_policy_state import advance_subscription_policy_state
from trading_agent.us_subscription_policy_state_store import SubscriptionPolicyStateStore

_CAPTURE_AT = quote_fixtures._snapshot().observed_at + dt.timedelta(seconds=1)
_EPOCH = "1" * 32


def test_live_capture_reobserves_and_projects_actionability(tmp_path: Path) -> None:
    request = _request(tmp_path)
    queue = ConnectorQueue([_connection()])
    clock = FixtureClock(_CAPTURE_AT)

    result = run_alpaca_sip_live_actionability(request, _dependencies(queue, clock, (_EPOCH,)))

    assert result.projection.appended is True
    assert result.reobserved_snapshot.observed_at > request.manifest.snapshot.observed_at
    assert queue.calls == 1
    assert len(AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3").records()) == 1


def test_live_capture_restart_replays_without_connector(tmp_path: Path) -> None:
    request = _request(tmp_path)
    first_queue = ConnectorQueue([_connection()])
    _ = run_alpaca_sip_live_actionability(
        request,
        _dependencies(first_queue, FixtureClock(_CAPTURE_AT), (_EPOCH,)),
    )
    replay_queue = ConnectorQueue([_connection()])

    replay = run_alpaca_sip_live_actionability(
        request,
        _dependencies(replay_queue, FixtureClock(_CAPTURE_AT + dt.timedelta(seconds=1)), ("2" * 32,)),
    )

    assert replay.projection.appended is False
    assert replay_queue.calls == 0


def test_next_minute_blocks_before_connector_and_output(tmp_path: Path) -> None:
    request = _request(tmp_path)
    queue = ConnectorQueue([_connection()])
    next_minute = _CAPTURE_AT.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)

    with pytest.raises(AlpacaSipLiveActionabilityError):
        _ = run_alpaca_sip_live_actionability(
            request,
            _dependencies(queue, FixtureClock(next_minute), (_EPOCH,)),
        )

    assert queue.calls == 0
    assert not (tmp_path / "receipts.sqlite3").exists()
    assert not (tmp_path / "actionability.sqlite3").exists()


def test_incomplete_quote_only_epoch_never_creates_actionability(tmp_path: Path) -> None:
    request = _request(tmp_path)
    quote = quote_fixtures._quote(100.01, 100.03, bid_size=300, ask_size=100)
    queue = ConnectorQueue(
        [
            FakeConnection(
                [
                    dynamic_fixtures._connected(),
                    dynamic_fixtures._authenticated(),
                    dynamic_fixtures._ack(),
                    dynamic_fixtures._frame(quote),
                ]
            )
        ]
    )

    with pytest.raises(AlpacaSipLiveActionabilityError):
        _ = run_alpaca_sip_live_actionability(
            request,
            _dependencies(queue, FixtureClock(_CAPTURE_AT), (_EPOCH,)),
        )

    assert queue.calls == 1
    assert (tmp_path / "receipts.sqlite3").exists()
    assert not (tmp_path / "actionability.sqlite3").exists()


def _request(tmp_path: Path) -> AlpacaSipLiveActionabilityRequest:
    first_state = advance_subscription_policy_state(None, _decision(dynamic_fixtures._NOW))
    current_at = quote_fixtures._snapshot().observed_at
    current_decision = build_subscription_policy_decision(
        BroadScannerSnapshot(_identity(), current_at - dt.timedelta(seconds=1), _candidates()),
        evaluated_at=current_at,
        active=first_state.active,
        cooldowns=first_state.cooldowns,
        config=_decision(dynamic_fixtures._NOW).config,
    )
    current_state = advance_subscription_policy_state(first_state, current_decision)
    policy_store = SubscriptionPolicyStateStore(tmp_path / "policy.sqlite3")
    assert policy_store.append(first_state)
    assert policy_store.append(current_state)
    plan_store = AlpacaSipDynamicPlanStore(tmp_path / "plans.sqlite3")
    plan = plan_store.roll(first_state).plan
    assert plan_store.roll(current_state).plan == plan
    manifest = build_alpaca_sip_quote_actionability_manifest(
        _base(entry="100.10", stop="99.00"),
        quote_fixtures._snapshot(),
        plan,
        scan_started_at=_SCAN_STARTED_AT,
    )
    return AlpacaSipLiveActionabilityRequest(
        AlpacaCredentials("fixture-key", "fixture-secret"),
        manifest,
        AlpacaSipLiveActionabilityStores(
            plan_store,
            policy_store,
            AlpacaSipDynamicReceiptStore(tmp_path / "receipts.sqlite3"),
            AlpacaSipQuoteActionabilityStore(tmp_path / "actionability.sqlite3"),
        ),
        AlpacaSipLiveActionabilityConfig(
            1,
            AlpacaSipDynamicBackoffConfig(1.0, 2.0, 4.0),
            1,
            1.0,
        ),
    )


def _dependencies(
    queue: ConnectorQueue,
    clock: FixtureClock,
    epochs: tuple[str, ...],
) -> AlpacaSipLiveActionabilityDependencies:
    return AlpacaSipLiveActionabilityDependencies(
        queue.connect,
        clock,
        iter(epochs).__next__,
        WaitRecorder(clock),
    )


def _connection() -> FakeConnection:
    quote = quote_fixtures._quote(100.01, 100.03, bid_size=300, ask_size=100)
    trade = trade_fixtures._trade(101, 100.02)
    return FakeConnection(
        [
            dynamic_fixtures._connected(),
            dynamic_fixtures._authenticated(),
            dynamic_fixtures._ack(),
            dynamic_fixtures._frame(quote, trade),
        ]
    )
