from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.alpaca_sip_dynamic_market_models import AlpacaSipDynamicMarketKind
from trading_agent.alpaca_sip_dynamic_projection import (
    AlpacaSipDynamicProjectionError,
    project_alpaca_sip_dynamic_receipts,
)
from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicRawReceipt,
    AlpacaSipDynamicReceiptKind,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
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
_EPOCH = "1" * 32
type _WireValue = str | int | float | list[str]
type _WireMessage = dict[str, _WireValue]


def test_verified_replay_projects_quote_and_trade_to_exact_instruments(tmp_path: Path) -> None:
    store = _store(tmp_path, _frame(_quote("BBB"), _trade("AAA")))

    projected = project_alpaca_sip_dynamic_receipts(store, _plan(), _EPOCH)

    assert tuple(item.kind for item in projected) == (
        AlpacaSipDynamicMarketKind.QUOTE,
        AlpacaSipDynamicMarketKind.TRADE,
    )
    assert tuple((item.symbol, item.instrument_id) for item in projected) == (
        ("BBB", "us-eq-b"),
        ("AAA", "us-eq-a"),
    )
    assert tuple(item.message_index for item in projected) == (0, 1)
    assert len({item.event_id for item in projected}) == 2
    assert all(item.raw_receipt_id for item in projected)


def test_correction_and_cancel_wire_messages_are_strictly_projected(tmp_path: Path) -> None:
    store = _store(tmp_path, _frame(_trade("AAA"), _correction(), _cancel()))

    projected = project_alpaca_sip_dynamic_receipts(store, _plan(), _EPOCH)

    assert tuple(item.kind for item in projected) == (
        AlpacaSipDynamicMarketKind.TRADE,
        AlpacaSipDynamicMarketKind.CORRECTION,
        AlpacaSipDynamicMarketKind.CANCEL,
    )


@pytest.mark.parametrize(
    "message_factory",
    (
        lambda: _quote("CCC"),
        lambda: _quote("BBB", timestamp="2026-07-17T14:00:01Z"),
        lambda: _quote("BBB", timestamp="2026-07-16T13:59:59Z"),
        lambda: {"T": "success", "msg": "connected"},
    ),
)
def test_unbound_future_or_control_data_message_fails_closed(
    tmp_path: Path,
    message_factory: Callable[[], _WireMessage],
) -> None:
    store = _store(tmp_path, _frame(message_factory()))

    with pytest.raises(AlpacaSipDynamicProjectionError):
        _ = project_alpaca_sip_dynamic_receipts(store, _plan(), _EPOCH)


def _store(tmp_path: Path, data_payload: bytes) -> AlpacaSipDynamicReceiptStore:
    store = AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")
    plan = _plan()
    store.bind_connection(_EPOCH, plan, _NOW)
    payloads = (_connected(), _authenticated(), _ack(), data_payload)
    for sequence, payload in enumerate(payloads, start=1):
        kind = AlpacaSipDynamicReceiptKind.CONTROL if sequence <= 3 else AlpacaSipDynamicReceiptKind.DATA
        _ = store.append_raw(
            plan,
            AlpacaSipDynamicRawReceipt(
                _EPOCH,
                sequence,
                _NOW + dt.timedelta(milliseconds=sequence),
                kind,
                payload,
            ),
        )
    return store


def _plan():
    identity = ResearchInputIdentity.from_verified_replay(
        "us_equities.opportunity.dynamic_subscription",
        CanonicalDatasetReplay("ds_projection", 2, "a" * 64, "b" * 64, "raw_projection", "c" * 64),
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


def _frame(*messages: _WireMessage) -> bytes:
    return json.dumps(messages, separators=(",", ":")).encode()


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


def _quote(symbol: str, timestamp: str = "2026-07-17T13:59:59Z") -> _WireMessage:
    return {
        "T": "q",
        "S": symbol,
        "ax": "V",
        "ap": 10.01,
        "as": 120,
        "bx": "V",
        "bp": 10.0,
        "bs": 100,
        "c": ["R"],
        "t": timestamp,
        "z": "C",
    }


def _trade(symbol: str) -> _WireMessage:
    return {
        "T": "t",
        "S": symbol,
        "i": 101,
        "x": "V",
        "p": 10.0,
        "s": 100,
        "c": ["@"],
        "t": "2026-07-17T13:59:59Z",
        "z": "C",
    }


def _correction() -> _WireMessage:
    return {
        "T": "c",
        "S": "AAA",
        "x": "V",
        "oi": 101,
        "op": 10.0,
        "os": 100,
        "oc": ["@"],
        "ci": 102,
        "cp": 10.01,
        "cs": 90,
        "cc": ["@"],
        "t": "2026-07-17T13:59:59Z",
        "z": "C",
    }


def _cancel() -> _WireMessage:
    return {
        "T": "x",
        "S": "AAA",
        "i": 102,
        "x": "V",
        "p": 10.01,
        "s": 90,
        "a": "C",
        "t": "2026-07-17T13:59:59Z",
        "z": "C",
    }
