from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.alpaca_http import AlpacaCredentials
from trading_agent.alpaca_sip_dynamic_connection_owner import run_alpaca_sip_dynamic_connection
from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicReceiptError,
    AlpacaSipDynamicReceiptKind,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import (
    AlpacaSipDynamicSubscriptionError,
    build_alpaca_sip_dynamic_subscription_plan,
)
from trading_agent.alpaca_sip_trade_stream import ALPACA_SIP_TRADE_STREAM_URL
from trading_agent.alpaca_sip_trade_stream_models import (
    AlpacaSipTradeStreamEndpointError,
    AlpacaSipTradeStreamProtocolError,
)
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_dynamic_subscription_policy import build_subscription_policy_decision
from trading_agent.us_subscription_models import (
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionPolicyConfig,
)

_NOW = dt.datetime(2026, 7, 17, 14, 0, tzinfo=dt.UTC)
_EPOCH = "e" * 32


class FakeConnection:
    __slots__ = ("_responses", "final_url", "sent", "timeouts")

    def __init__(self, responses: list[bytes], final_url: str = ALPACA_SIP_TRADE_STREAM_URL) -> None:
        self._responses = responses
        self.final_url = final_url
        self.sent: list[str] = []
        self.timeouts: list[float | None] = []

    def send(self, message: str) -> None:
        self.sent.append(message)

    def recv(self, timeout: float | None = None) -> str | bytes:
        self.timeouts.append(timeout)
        if not self._responses:
            raise TimeoutError
        return self._responses.pop(0)


def test_owner_binds_plan_and_persists_bounded_raw_frames(tmp_path: Path) -> None:
    connection = FakeConnection([_connected(), _authenticated(), _ack(), _quote(), _trade()])
    store = AlpacaSipDynamicReceiptStore(tmp_path / "nested" / "dynamic.sqlite3")
    times = iter(_times(6))

    evidence = run_alpaca_sip_dynamic_connection(
        _credentials(),
        _plan(),
        store,
        max_data_frames=2,
        timeout_seconds=1.0,
        connector=_connector(connection),
        _clock=times.__next__,
        _epoch_factory=lambda: _EPOCH,
    )

    replay = store.load_replay(_plan(), _EPOCH)
    assert json.loads(connection.sent[0]) == {
        "action": "auth",
        "key": "fixture-key",
        "secret": "fixture-secret",
    }
    assert connection.sent[1] == '{"action":"subscribe","quotes":["BBB","AAA"],"trades":["BBB","AAA"]}'
    assert connection.timeouts == [5.0, 5.0, 5.0, 1.0, 1.0]
    assert tuple(item.sequence for item in replay) == (1, 2, 3, 4, 5)
    assert tuple(item.kind for item in replay) == (
        AlpacaSipDynamicReceiptKind.CONTROL,
        AlpacaSipDynamicReceiptKind.CONTROL,
        AlpacaSipDynamicReceiptKind.CONTROL,
        AlpacaSipDynamicReceiptKind.DATA,
        AlpacaSipDynamicReceiptKind.DATA,
    )
    assert evidence.receipt_ids == tuple(item.receipt_id for item in replay)
    assert evidence.completed_at == replay[-1].received_at


def test_invalid_subscription_ack_is_preserved_before_failure(tmp_path: Path) -> None:
    connection = FakeConnection([_connected(), _authenticated(), b"[]"])
    store = AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")

    with pytest.raises(AlpacaSipDynamicSubscriptionError):
        _ = _run(connection, store)

    replay = store.load_replay(_plan(), _EPOCH)
    assert len(replay) == 3
    assert replay[-1].payload == b"[]"


def test_invalid_auth_control_is_preserved_before_failure(tmp_path: Path) -> None:
    connection = FakeConnection([_connected(), b"[]"])
    store = AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")

    with pytest.raises(AlpacaSipTradeStreamProtocolError):
        _ = _run(connection, store)

    assert len(store.load_replay(_plan(), _EPOCH)) == 2
    assert len(connection.sent) == 1


def test_changed_final_url_blocks_credentials_and_frames(tmp_path: Path) -> None:
    connection = FakeConnection([], "wss://stream.data.alpaca.markets/v2/iex")
    store = AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")

    with pytest.raises(AlpacaSipTradeStreamEndpointError):
        _ = _run(connection, store)

    assert connection.sent == []
    assert store.load_replay(_plan(), _EPOCH) == ()


def test_data_timeout_preserves_completed_handshake_receipts(tmp_path: Path) -> None:
    connection = FakeConnection([_connected(), _authenticated(), _ack()])
    store = AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")

    with pytest.raises(TimeoutError):
        _ = _run(connection, store)

    assert len(store.load_replay(_plan(), _EPOCH)) == 3


def test_owner_lease_blocks_second_connection_before_connector(tmp_path: Path) -> None:
    connection = FakeConnection([_connected(), _authenticated(), _ack(), _quote()])
    store = AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")
    nested_connector_called = False

    @contextmanager
    def never_connector(_url: str) -> Iterator[FakeConnection]:
        nonlocal nested_connector_called
        nested_connector_called = True
        yield FakeConnection([])

    @contextmanager
    def outer_connector(_url: str) -> Iterator[FakeConnection]:
        with pytest.raises(AlpacaSipDynamicReceiptError):
            _ = run_alpaca_sip_dynamic_connection(
                _credentials(),
                _plan(),
                store,
                max_data_frames=1,
                timeout_seconds=1.0,
                connector=never_connector,
                _clock=iter(_times(2)).__next__,
                _epoch_factory=lambda: "f" * 32,
            )
        yield connection

    _ = run_alpaca_sip_dynamic_connection(
        _credentials(),
        _plan(),
        store,
        max_data_frames=1,
        timeout_seconds=1.0,
        connector=outer_connector,
        _clock=iter(_times(5)).__next__,
        _epoch_factory=lambda: _EPOCH,
    )

    assert nested_connector_called is False


def _run(connection: FakeConnection, store: AlpacaSipDynamicReceiptStore):
    return run_alpaca_sip_dynamic_connection(
        _credentials(),
        _plan(),
        store,
        max_data_frames=1,
        timeout_seconds=1.0,
        connector=_connector(connection),
        _clock=iter(_times(6)).__next__,
        _epoch_factory=lambda: _EPOCH,
    )


def _connector(connection: FakeConnection):
    @contextmanager
    def connector(_: str) -> Iterator[FakeConnection]:
        yield connection

    return connector


def _credentials() -> AlpacaCredentials:
    return AlpacaCredentials("fixture-key", "fixture-secret")


def _times(count: int) -> tuple[dt.datetime, ...]:
    return tuple(_NOW + dt.timedelta(milliseconds=index) for index in range(count))


def _plan():
    identity = ResearchInputIdentity.from_verified_replay(
        "us_equities.opportunity.dynamic_subscription",
        CanonicalDatasetReplay("ds_owner", 2, "a" * 64, "b" * 64, "raw_owner", "c" * 64),
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


def _quote() -> bytes:
    return b'[{"T":"q","S":"BBB","bp":10.0,"ap":10.01}]'


def _trade() -> bytes:
    return b'[{"T":"t","S":"AAA","p":11.0,"s":100}]'
