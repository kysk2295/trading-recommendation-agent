from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from tests.alpaca_sip_dynamic_reconnect_fixtures import (
    ConnectorQueue,
    FixtureClock,
    WaitRecorder,
    invalid_ack_connection,
    success_connection,
    timeout_connection,
)
from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_dynamic_backoff import AlpacaSipDynamicBackoffConfig
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_reconnect_supervisor import (
    AlpacaSipDynamicReconnectRunStatus,
    run_alpaca_sip_dynamic_reconnect_supervisor,
)
from trading_agent.alpaca_sip_dynamic_subscription import build_alpaca_sip_dynamic_subscription_plan
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_dynamic_subscription_policy import build_subscription_policy_decision
from trading_agent.us_subscription_models import (
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionPolicyConfig,
)

_NOW = dt.datetime(2026, 7, 17, 14, 0, tzinfo=dt.UTC)


def test_retryable_timeout_then_success_completes_within_budget(tmp_path: Path) -> None:
    queue = ConnectorQueue([timeout_connection(), success_connection()])
    clock = FixtureClock(_NOW)
    waiter = WaitRecorder(clock)

    report = _run(
        tmp_path,
        queue,
        max_attempts=2,
        epochs=("1" * 32, "2" * 32),
        clock=clock,
        waiter=waiter,
    )

    assert report.status is AlpacaSipDynamicReconnectRunStatus.BOUNDED_COMPLETE
    assert report.attempted_this_run == 2
    assert report.completed_attempts == 2
    assert report.remaining_attempts == 0
    assert report.connection_evidence is not None
    assert queue.calls == 2
    assert len(waiter.delays) == 1
    assert 0.99 < waiter.delays[0] <= 1.0


def test_restart_restores_failed_attempt_before_exhausting_budget(tmp_path: Path) -> None:
    path = tmp_path / "dynamic.sqlite3"
    first = ConnectorQueue([timeout_connection()])
    _ = _run_path(
        path,
        first,
        max_attempts=1,
        epochs=("1" * 32,),
        clock=FixtureClock(_NOW),
    )
    restarted = ConnectorQueue([timeout_connection()])
    clock = FixtureClock(_NOW + dt.timedelta(milliseconds=504))
    waiter = WaitRecorder(clock)

    report = _run_path(
        path,
        restarted,
        max_attempts=2,
        epochs=("2" * 32,),
        clock=clock,
        waiter=waiter,
    )

    assert report.status is AlpacaSipDynamicReconnectRunStatus.BLOCKED_BUDGET
    assert report.attempted_this_run == 1
    assert report.completed_attempts == 2
    assert report.remaining_attempts == 0
    assert restarted.calls == 1
    assert waiter.delays == [0.501]


def test_existing_complete_history_short_circuits_connector(tmp_path: Path) -> None:
    path = tmp_path / "dynamic.sqlite3"
    first = ConnectorQueue([success_connection()])
    _ = _run_path(
        path,
        first,
        max_attempts=3,
        epochs=("1" * 32,),
        clock=FixtureClock(_NOW),
    )
    unused = ConnectorQueue([success_connection()])

    report = _run_path(
        path,
        unused,
        max_attempts=3,
        epochs=("2" * 32,),
        clock=FixtureClock(_NOW),
    )

    assert report.status is AlpacaSipDynamicReconnectRunStatus.BLOCKED_COMPLETE
    assert report.attempted_this_run == 0
    assert report.completed_attempts == 1
    assert unused.calls == 0


def test_invalid_ack_is_non_retryable_and_does_not_consume_next_connector(tmp_path: Path) -> None:
    queue = ConnectorQueue([invalid_ack_connection(), success_connection()])

    report = _run(tmp_path, queue, max_attempts=3, epochs=("1" * 32, "2" * 32))

    assert report.status is AlpacaSipDynamicReconnectRunStatus.BLOCKED_NON_RETRYABLE
    assert report.attempted_this_run == 1
    assert report.completed_attempts == 1
    assert report.remaining_attempts == 2
    assert report.connection_evidence is None
    assert queue.calls == 1


def test_interrupt_during_backoff_stops_before_next_connector(tmp_path: Path) -> None:
    queue = ConnectorQueue([timeout_connection(), success_connection()])
    clock = FixtureClock(_NOW)
    waiter = WaitRecorder(clock, interrupted=True)

    report = _run(
        tmp_path,
        queue,
        max_attempts=2,
        epochs=("1" * 32, "2" * 32),
        clock=clock,
        waiter=waiter,
    )

    assert report.status is AlpacaSipDynamicReconnectRunStatus.STOPPED
    assert report.attempted_this_run == 1
    assert report.completed_attempts == 1
    assert queue.calls == 1
    assert len(waiter.delays) == 1


def test_restart_clock_regression_blocks_connector(tmp_path: Path) -> None:
    path = tmp_path / "dynamic.sqlite3"
    _ = _run_path(
        path,
        ConnectorQueue([timeout_connection()]),
        max_attempts=1,
        epochs=("1" * 32,),
        clock=FixtureClock(_NOW),
    )
    restarted = ConnectorQueue([success_connection()])

    report = _run_path(
        path,
        restarted,
        max_attempts=2,
        epochs=("2" * 32,),
        clock=FixtureClock(_NOW),
    )

    assert report.status is AlpacaSipDynamicReconnectRunStatus.BLOCKED_CLOCK_REGRESSION
    assert report.attempted_this_run == 0
    assert report.completed_attempts == 1
    assert restarted.calls == 0


def _run(
    tmp_path: Path,
    queue: ConnectorQueue,
    *,
    max_attempts: int,
    epochs: tuple[str, ...],
    clock: FixtureClock | None = None,
    waiter: WaitRecorder | None = None,
):
    return _run_path(
        tmp_path / "dynamic.sqlite3",
        queue,
        max_attempts=max_attempts,
        epochs=epochs,
        clock=FixtureClock(_NOW) if clock is None else clock,
        waiter=waiter,
    )


def _run_path(
    path: Path,
    queue: ConnectorQueue,
    *,
    max_attempts: int,
    epochs: tuple[str, ...],
    clock: FixtureClock,
    waiter: WaitRecorder | None = None,
):
    return run_alpaca_sip_dynamic_reconnect_supervisor(
        _credentials(),
        _plan(),
        AlpacaSipDynamicReceiptStore(path),
        max_attempts=max_attempts,
        backoff=AlpacaSipDynamicBackoffConfig(1.0, 2.0, 4.0),
        max_data_frames=1,
        timeout_seconds=1.0,
        connector=queue.connect,
        _clock=clock,
        _epoch_factory=iter(epochs).__next__,
        _wait=WaitRecorder(clock) if waiter is None else waiter,
    )


def _credentials() -> AlpacaCredentials:
    return AlpacaCredentials("fixture-key", "fixture-secret")


def _plan():
    identity = ResearchInputIdentity.from_verified_replay(
        "us_equities.opportunity.dynamic_subscription",
        CanonicalDatasetReplay("ds_supervisor", 2, "a" * 64, "b" * 64, "raw_supervisor", "c" * 64),
    )
    snapshot = BroadScannerSnapshot(
        identity,
        _NOW - dt.timedelta(seconds=10),
        (
            BroadScannerCandidate("us-eq-a", "AAA", Decimal("9.5"), 2),
            BroadScannerCandidate("us-eq-b", "BBB", Decimal("10"), 4),
        ),
    )
    return build_alpaca_sip_dynamic_subscription_plan(
        build_subscription_policy_decision(
            snapshot,
            evaluated_at=_NOW,
            active=(),
            cooldowns=(),
            config=SubscriptionPolicyConfig(
                2,
                dt.timedelta(seconds=30),
                dt.timedelta(minutes=2),
                dt.timedelta(minutes=5),
            ),
        )
    )
