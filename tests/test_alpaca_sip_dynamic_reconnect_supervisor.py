from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_reconnect_supervisor import (
    AlpacaSipDynamicReconnectRunStatus,
    run_alpaca_sip_dynamic_reconnect_supervisor,
)
from trading_agent.alpaca_sip_dynamic_subscription import build_alpaca_sip_dynamic_subscription_plan
from trading_agent.alpaca_sip_trade_stream import ALPACA_SIP_TRADE_STREAM_URL
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_dynamic_subscription_policy import build_subscription_policy_decision
from trading_agent.us_subscription_models import (
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionPolicyConfig,
)

_NOW = dt.datetime(2026, 7, 17, 14, 0, tzinfo=dt.UTC)


class FakeConnection:
    __slots__ = ("_responses", "final_url", "sent")

    def __init__(self, responses: list[bytes]) -> None:
        self._responses = responses
        self.final_url = ALPACA_SIP_TRADE_STREAM_URL
        self.sent: list[str] = []

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> str | bytes:
        _ = timeout
        if not self._responses:
            raise TimeoutError
        return self._responses.pop(0)


class ConnectorQueue:
    __slots__ = ("_connections", "calls")

    def __init__(self, connections: list[FakeConnection]) -> None:
        self._connections = connections
        self.calls = 0

    @contextmanager
    def connect(self, url: str) -> Iterator[FakeConnection]:
        assert url == ALPACA_SIP_TRADE_STREAM_URL
        self.calls += 1
        yield self._connections.pop(0)


def test_retryable_timeout_then_success_completes_within_budget(tmp_path: Path) -> None:
    queue = ConnectorQueue([_timeout_connection(), _success_connection()])

    report = _run(tmp_path, queue, max_attempts=2, epochs=("1" * 32, "2" * 32))

    assert report.status is AlpacaSipDynamicReconnectRunStatus.BOUNDED_COMPLETE
    assert report.attempted_this_run == 2
    assert report.completed_attempts == 2
    assert report.remaining_attempts == 0
    assert report.connection_evidence is not None
    assert queue.calls == 2


def test_restart_restores_failed_attempt_before_exhausting_budget(tmp_path: Path) -> None:
    path = tmp_path / "dynamic.sqlite3"
    first = ConnectorQueue([_timeout_connection()])
    _ = _run_path(path, first, max_attempts=1, epochs=("1" * 32,))
    restarted = ConnectorQueue([_timeout_connection()])

    report = _run_path(path, restarted, max_attempts=2, epochs=("2" * 32,))

    assert report.status is AlpacaSipDynamicReconnectRunStatus.BLOCKED_BUDGET
    assert report.attempted_this_run == 1
    assert report.completed_attempts == 2
    assert report.remaining_attempts == 0
    assert restarted.calls == 1


def test_existing_complete_history_short_circuits_connector(tmp_path: Path) -> None:
    path = tmp_path / "dynamic.sqlite3"
    first = ConnectorQueue([_success_connection()])
    _ = _run_path(path, first, max_attempts=3, epochs=("1" * 32,))
    unused = ConnectorQueue([_success_connection()])

    report = _run_path(path, unused, max_attempts=3, epochs=("2" * 32,))

    assert report.status is AlpacaSipDynamicReconnectRunStatus.BLOCKED_COMPLETE
    assert report.attempted_this_run == 0
    assert report.completed_attempts == 1
    assert unused.calls == 0


def test_invalid_ack_is_non_retryable_and_does_not_consume_next_connector(tmp_path: Path) -> None:
    queue = ConnectorQueue([_invalid_ack_connection(), _success_connection()])

    report = _run(tmp_path, queue, max_attempts=3, epochs=("1" * 32, "2" * 32))

    assert report.status is AlpacaSipDynamicReconnectRunStatus.BLOCKED_NON_RETRYABLE
    assert report.attempted_this_run == 1
    assert report.completed_attempts == 1
    assert report.remaining_attempts == 2
    assert report.connection_evidence is None
    assert queue.calls == 1


def _run(
    tmp_path: Path,
    queue: ConnectorQueue,
    *,
    max_attempts: int,
    epochs: tuple[str, ...],
):
    return _run_path(tmp_path / "dynamic.sqlite3", queue, max_attempts=max_attempts, epochs=epochs)


def _run_path(
    path: Path,
    queue: ConnectorQueue,
    *,
    max_attempts: int,
    epochs: tuple[str, ...],
):
    return run_alpaca_sip_dynamic_reconnect_supervisor(
        _credentials(),
        _plan(),
        AlpacaSipDynamicReceiptStore(path),
        max_attempts=max_attempts,
        max_data_frames=1,
        timeout_seconds=1.0,
        connector=queue.connect,
        _clock=iter(_times(32)).__next__,
        _epoch_factory=iter(epochs).__next__,
    )


def _credentials() -> AlpacaCredentials:
    return AlpacaCredentials("fixture-key", "fixture-secret")


def _times(count: int) -> tuple[dt.datetime, ...]:
    return tuple(_NOW + dt.timedelta(milliseconds=index) for index in range(count))


def _timeout_connection() -> FakeConnection:
    return FakeConnection([_connected(), _authenticated(), _ack()])


def _success_connection() -> FakeConnection:
    return FakeConnection([_connected(), _authenticated(), _ack(), b'[{"T":"q","S":"BBB"}]'])


def _invalid_ack_connection() -> FakeConnection:
    return FakeConnection([_connected(), _authenticated(), b"[]"])


def _connected() -> bytes:
    return b'[{"T":"success","msg":"connected"}]'


def _authenticated() -> bytes:
    return b'[{"T":"success","msg":"authenticated"}]'


def _ack() -> bytes:
    return (
        b'[{"T":"subscription","trades":["BBB","AAA"],"quotes":["BBB","AAA"],'
        b'"bars":[],"updatedBars":[],"dailyBars":[],"statuses":[],"lulds":[],'
        b'"corrections":["BBB","AAA"],"cancelErrors":["BBB","AAA"]}]'
    )


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
