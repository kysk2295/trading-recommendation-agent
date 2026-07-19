from __future__ import annotations

import datetime as dt
import os
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from trading_agent.alpaca_sip_dynamic_receipt_models import (
    AlpacaSipDynamicRawReceipt,
    AlpacaSipDynamicReceiptError,
    AlpacaSipDynamicReceiptKind,
)
from trading_agent.alpaca_sip_dynamic_receipt_store import AlpacaSipDynamicReceiptStore
from trading_agent.alpaca_sip_dynamic_subscription import (
    AlpacaSipDynamicSubscriptionPlan,
    build_alpaca_sip_dynamic_subscription_plan,
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
_EPOCH = "d" * 32


def test_raw_receipts_are_plan_bound_append_only_and_replayable(tmp_path: Path) -> None:
    store = AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")
    plan = _plan(_NOW)
    store.bind_connection(_EPOCH, plan, _NOW)

    control = store.append_raw(plan, _receipt(1, AlpacaSipDynamicReceiptKind.CONTROL, _ack()))
    data = store.append_raw(plan, _receipt(2, AlpacaSipDynamicReceiptKind.DATA, _quote()))
    replay = store.load_replay(plan, _EPOCH)

    assert control.generation == 1
    assert data.generation == 2
    assert tuple(item.sequence for item in replay) == (1, 2)
    assert tuple(item.kind for item in replay) == (
        AlpacaSipDynamicReceiptKind.CONTROL,
        AlpacaSipDynamicReceiptKind.DATA,
    )
    assert tuple(item.payload for item in replay) == (_ack(), _quote())
    assert len({item.receipt_id for item in replay}) == 2
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_exact_retry_is_idempotent_but_conflicting_sequence_fails_closed(tmp_path: Path) -> None:
    store = _bound_store(tmp_path)
    plan = _plan(_NOW)
    receipt = _receipt(1, AlpacaSipDynamicReceiptKind.CONTROL, _ack())

    first = store.append_raw(plan, receipt)
    second = store.append_raw(plan, receipt)

    assert first == second
    with pytest.raises(AlpacaSipDynamicReceiptError):
        _ = store.append_raw(plan, _receipt(1, AlpacaSipDynamicReceiptKind.DATA, _quote()))


def test_sequence_gap_fails_before_any_raw_row_is_written(tmp_path: Path) -> None:
    store = _bound_store(tmp_path)

    with pytest.raises(AlpacaSipDynamicReceiptError):
        _ = store.append_raw(_plan(_NOW), _receipt(2, AlpacaSipDynamicReceiptKind.DATA, _quote()))

    assert store.load_replay(_plan(_NOW), _EPOCH) == ()


def test_unbound_connection_cannot_append_raw_receipt(tmp_path: Path) -> None:
    store = AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")

    with pytest.raises(AlpacaSipDynamicReceiptError):
        _ = store.append_raw(
            _plan(_NOW),
            _receipt(1, AlpacaSipDynamicReceiptKind.CONTROL, _ack()),
        )


def test_receipt_before_connection_binding_fails_closed(tmp_path: Path) -> None:
    store = _bound_store(tmp_path)
    receipt = AlpacaSipDynamicRawReceipt(
        _EPOCH,
        1,
        _NOW - dt.timedelta(microseconds=1),
        AlpacaSipDynamicReceiptKind.CONTROL,
        _ack(),
    )

    with pytest.raises(AlpacaSipDynamicReceiptError):
        _ = store.append_raw(_plan(_NOW), receipt)


def test_connection_cannot_be_replayed_or_written_with_another_plan(tmp_path: Path) -> None:
    store = _bound_store(tmp_path)
    original = _plan(_NOW)
    other = _plan(_NOW + dt.timedelta(minutes=1))

    with pytest.raises(AlpacaSipDynamicReceiptError):
        _ = store.append_raw(other, _receipt(1, AlpacaSipDynamicReceiptKind.CONTROL, _ack()))
    with pytest.raises(AlpacaSipDynamicReceiptError):
        _ = store.load_replay(other, _EPOCH)

    assert store.load_replay(original, _EPOCH) == ()


def test_mutation_trigger_and_payload_integrity_are_enforced(tmp_path: Path) -> None:
    store = _bound_store(tmp_path)
    plan = _plan(_NOW)
    _ = store.append_raw(plan, _receipt(1, AlpacaSipDynamicReceiptKind.CONTROL, _ack()))

    with sqlite3.connect(store.path) as database, pytest.raises(sqlite3.IntegrityError):
        _ = database.execute("UPDATE dynamic_receipts SET payload=x'5b5d'")
    with sqlite3.connect(store.path) as database:
        database.executescript(
            "DROP TRIGGER dynamic_receipts_no_update;"
            "UPDATE dynamic_receipts SET payload=x'5b5d';"
            "CREATE TRIGGER dynamic_receipts_no_update BEFORE UPDATE ON dynamic_receipts "
            "BEGIN SELECT RAISE(ABORT,'append-only'); END;"
        )

    with pytest.raises(AlpacaSipDynamicReceiptError):
        _ = store.load_replay(plan, _EPOCH)


def test_non_private_or_symlink_store_fails_closed(tmp_path: Path) -> None:
    store = _bound_store(tmp_path)
    os.chmod(store.path, 0o644)

    with pytest.raises(AlpacaSipDynamicReceiptError):
        _ = store.load_replay(_plan(_NOW), _EPOCH)

    symlink = tmp_path / "linked.sqlite3"
    symlink.symlink_to(store.path)
    with pytest.raises(AlpacaSipDynamicReceiptError):
        _ = AlpacaSipDynamicReceiptStore(symlink).load_replay(_plan(_NOW), _EPOCH)


def test_hard_linked_store_fails_closed(tmp_path: Path) -> None:
    store = _bound_store(tmp_path)
    os.link(store.path, tmp_path / "second-link.sqlite3")

    with pytest.raises(AlpacaSipDynamicReceiptError):
        _ = store.load_replay(_plan(_NOW), _EPOCH)


def test_unexpected_schema_object_fails_closed(tmp_path: Path) -> None:
    store = _bound_store(tmp_path)
    with sqlite3.connect(store.path) as database:
        _ = database.execute("CREATE TABLE injected (value TEXT)")

    with pytest.raises(AlpacaSipDynamicReceiptError):
        _ = store.load_replay(_plan(_NOW), _EPOCH)


def _bound_store(tmp_path: Path) -> AlpacaSipDynamicReceiptStore:
    store = AlpacaSipDynamicReceiptStore(tmp_path / "dynamic.sqlite3")
    store.bind_connection(_EPOCH, _plan(_NOW), _NOW)
    return store


def _receipt(
    sequence: int,
    kind: AlpacaSipDynamicReceiptKind,
    payload: bytes,
) -> AlpacaSipDynamicRawReceipt:
    return AlpacaSipDynamicRawReceipt(
        _EPOCH,
        sequence,
        _NOW + dt.timedelta(milliseconds=sequence),
        kind,
        payload,
    )


def _plan(evaluated_at: dt.datetime) -> AlpacaSipDynamicSubscriptionPlan:
    identity = ResearchInputIdentity.from_verified_replay(
        "us_equities.opportunity.dynamic_subscription",
        CanonicalDatasetReplay("ds_receipt", 2, "a" * 64, "b" * 64, "raw_receipt", "c" * 64),
    )
    snapshot = BroadScannerSnapshot(
        identity,
        evaluated_at - dt.timedelta(seconds=10),
        (
            BroadScannerCandidate("us-eq-a", "AAA", Decimal("9.5"), 2),
            BroadScannerCandidate("us-eq-b", "BBB", Decimal("10"), 4),
        ),
    )
    decision = build_subscription_policy_decision(
        snapshot,
        evaluated_at=evaluated_at,
        active=(),
        cooldowns=(),
        config=SubscriptionPolicyConfig(
            2,
            dt.timedelta(seconds=30),
            dt.timedelta(minutes=2),
            dt.timedelta(minutes=5),
        ),
    )
    return build_alpaca_sip_dynamic_subscription_plan(decision)


def _ack() -> bytes:
    return b'[{"T":"subscription","trades":["BBB","AAA"],"quotes":["BBB","AAA"]}]'


def _quote() -> bytes:
    return b'[{"T":"q","S":"BBB","bp":10.0,"ap":10.01}]'
