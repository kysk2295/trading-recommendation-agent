from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicRawReceipt,
    AlpacaSipDynamicReceiptKind,
    AlpacaSipDynamicTerminalStatus,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_reconnect_policy import (
    AlpacaSipDynamicReconnectError,
    AlpacaSipDynamicReconnectStatus,
    decide_alpaca_sip_dynamic_reconnect,
)
from trading_agent.alpaca_sip_dynamic_subscription import build_alpaca_sip_dynamic_subscription_plan
from trading_agent.alpaca_sip_dynamic_terminal_store import AlpacaSipDynamicTerminalStore
from trading_agent.canonical_duckdb_replay import CanonicalDatasetReplay
from trading_agent.research_input_identity import ResearchInputIdentity
from trading_agent.us_dynamic_subscription_policy import build_subscription_policy_decision
from trading_agent.us_subscription_models import (
    BroadScannerCandidate,
    BroadScannerSnapshot,
    SubscriptionPolicyConfig,
)

_NOW = dt.datetime(2026, 7, 17, 14, 0, tzinfo=dt.UTC)


def test_empty_history_allows_first_bounded_attempt(tmp_path: Path) -> None:
    history = AlpacaSipDynamicTerminalStore(tmp_path / "dynamic.sqlite3").load_history(_plan())

    decision = decide_alpaca_sip_dynamic_reconnect(history, max_attempts=3)

    assert decision.status is AlpacaSipDynamicReconnectStatus.READY
    assert decision.completed_attempts == 0
    assert decision.next_attempt_number == 1
    assert decision.remaining_attempts == 3


def test_failed_history_restores_next_attempt_and_remaining_budget(tmp_path: Path) -> None:
    terminals = _failed_history(tmp_path, 2)

    decision = decide_alpaca_sip_dynamic_reconnect(terminals.load_history(_plan()), max_attempts=3)

    assert decision.status is AlpacaSipDynamicReconnectStatus.READY
    assert decision.completed_attempts == 2
    assert decision.next_attempt_number == 3
    assert decision.remaining_attempts == 1


def test_exhausted_failed_history_blocks_new_attempt(tmp_path: Path) -> None:
    terminals = _failed_history(tmp_path, 3)

    decision = decide_alpaca_sip_dynamic_reconnect(terminals.load_history(_plan()), max_attempts=3)

    assert decision.status is AlpacaSipDynamicReconnectStatus.BLOCKED_BUDGET
    assert decision.next_attempt_number is None
    assert decision.remaining_attempts == 0


def test_any_bounded_complete_epoch_blocks_reconnect(tmp_path: Path) -> None:
    terminals = _failed_history(tmp_path, 1)
    _append_terminal(terminals, 2, AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE)

    decision = decide_alpaca_sip_dynamic_reconnect(terminals.load_history(_plan()), max_attempts=3)

    assert decision.status is AlpacaSipDynamicReconnectStatus.BLOCKED_COMPLETE
    assert decision.next_attempt_number is None
    assert decision.remaining_attempts == 1


def test_unordered_or_mixed_plan_history_fails_closed(tmp_path: Path) -> None:
    terminals = _failed_history(tmp_path, 2).load_history(_plan())

    with pytest.raises(AlpacaSipDynamicReconnectError):
        _ = decide_alpaca_sip_dynamic_reconnect(tuple(reversed(terminals)), max_attempts=3)


def test_attempt_after_bounded_complete_is_invalid_history(tmp_path: Path) -> None:
    terminals = _failed_history(tmp_path, 1)
    _append_terminal(terminals, 2, AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE)
    _append_terminal(terminals, 3, AlpacaSipDynamicTerminalStatus.FAILED)

    with pytest.raises(AlpacaSipDynamicReconnectError):
        _ = decide_alpaca_sip_dynamic_reconnect(terminals.load_history(_plan()), max_attempts=3)


def _failed_history(tmp_path: Path, count: int) -> AlpacaSipDynamicTerminalStore:
    terminals = AlpacaSipDynamicTerminalStore(tmp_path / "dynamic.sqlite3")
    for attempt in range(1, count + 1):
        _append_terminal(terminals, attempt, AlpacaSipDynamicTerminalStatus.FAILED)
    return terminals


def _append_terminal(
    terminals: AlpacaSipDynamicTerminalStore,
    attempt: int,
    status: AlpacaSipDynamicTerminalStatus,
) -> None:
    epoch = f"{attempt:032x}"
    store = AlpacaSipDynamicReceiptStore(terminals.path)
    store.bind_connection(epoch, _plan(), _NOW + dt.timedelta(seconds=attempt))
    if status is AlpacaSipDynamicTerminalStatus.BOUNDED_COMPLETE:
        for sequence in range(1, 5):
            kind = AlpacaSipDynamicReceiptKind.CONTROL if sequence <= 3 else AlpacaSipDynamicReceiptKind.DATA
            _ = store.append_raw(
                _plan(),
                AlpacaSipDynamicRawReceipt(
                    epoch,
                    sequence,
                    _NOW + dt.timedelta(seconds=attempt, milliseconds=sequence),
                    kind,
                    f"frame-{sequence}".encode(),
                ),
            )
    _ = terminals.append(
        _plan(),
        epoch,
        _NOW + dt.timedelta(seconds=attempt, milliseconds=10),
        status,
    )


def _plan():
    identity = ResearchInputIdentity.from_verified_replay(
        "us_equities.opportunity.dynamic_subscription",
        CanonicalDatasetReplay("ds_reconnect", 2, "a" * 64, "b" * 64, "raw_reconnect", "c" * 64),
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
