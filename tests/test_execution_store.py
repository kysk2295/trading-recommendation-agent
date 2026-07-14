from __future__ import annotations

import datetime as dt
import sqlite3
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading_agent.execution_store import (
    AccountBindingConflictError,
    BrokerEventConflictError,
    ExecutionStore,
    InactiveExecutionWriterError,
    IntentConflictError,
    WriterLeaseUnavailableError,
)
from trading_agent.paper_execution_models import (
    AccountFingerprint,
    BrokerEventKey,
    BrokerOrderEvent,
    BrokerOrderEventType,
    BrokerOrderId,
    IntentId,
    PaperOrderIntent,
    PaperOrderSide,
)


def _intent(entry: float = 10.0) -> PaperOrderIntent:
    return PaperOrderIntent(
        intent_id=IntentId("orb-v1-20260714-AAA-093600"),
        strategy_id="orb",
        strategy_version="1.0.0",
        symbol="AAA",
        created_at=dt.datetime(
            2026,
            7,
            14,
            9,
            36,
            tzinfo=ZoneInfo("America/New_York"),
        ),
        side=PaperOrderSide.BUY,
        entry_limit=entry,
        stop=9.75,
        target_1r=10.25,
        target_2r=10.5,
    )


def test_reader_does_not_create_a_missing_database(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "missing/execution.sqlite3"
    store = ExecutionStore(database)

    # When
    intents = store.intents()

    # Then
    assert intents == ()
    assert not database.exists()
    assert not database.parent.exists()


def test_single_writer_inserts_an_intent_once(tmp_path: Path) -> None:
    # Given
    store = ExecutionStore(tmp_path / "execution.sqlite3")

    # When
    with store.writer() as writer:
        inserted = writer.save_intent(_intent(), quantity=259)
        repeated = writer.save_intent(_intent(), quantity=259)

    # Then
    assert inserted is True
    assert repeated is False
    assert len(store.intents()) == 1
    assert store.intents()[0].quantity == 259


def test_same_intent_id_with_different_fields_is_rejected(tmp_path: Path) -> None:
    # Given
    store = ExecutionStore(tmp_path / "execution.sqlite3")

    # When / Then
    with store.writer() as writer:
        assert writer.save_intent(_intent(), quantity=259) is True
        with pytest.raises(IntentConflictError, match="immutable"):
            _ = writer.save_intent(_intent(entry=10.1), quantity=259)


def test_broker_events_are_append_only_and_deduplicated(tmp_path: Path) -> None:
    # Given
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    occurred_at = _intent().created_at

    # When
    with store.writer() as writer:
        _ = writer.save_intent(_intent(), quantity=259)
        event = BrokerOrderEvent(
            event_key=BrokerEventKey("paper-order-1:accepted:0"),
            intent_id=_intent().intent_id,
            occurred_at=occurred_at,
            event_type=BrokerOrderEventType.ACCEPTED,
            broker_order_id=BrokerOrderId("paper-order-1"),
            payload_json='{"status":"accepted"}',
        )
        first = writer.append_broker_event(event)
        repeated = writer.append_broker_event(event)

    # Then
    assert first is True
    assert repeated is False
    assert [event.event_type for event in store.broker_events(_intent().intent_id)] == [
        BrokerOrderEventType.ACCEPTED
    ]


def test_second_writer_fails_before_database_or_http_work(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "execution.sqlite3"
    first_store = ExecutionStore(database)
    second_store = ExecutionStore(database)

    # When / Then
    with (
        first_store.writer(),
        pytest.raises(WriterLeaseUnavailableError, match="이미 실행 중"),
        second_store.writer(),
    ):
        pytest.fail("두 번째 writer가 실행되면 안 됩니다")


def test_readers_observe_committed_rows_while_writer_holds_lease(tmp_path: Path) -> None:
    # Given
    store = ExecutionStore(tmp_path / "execution.sqlite3")

    # When
    with store.writer() as writer:
        _ = writer.save_intent(_intent(), quantity=259)
        visible = store.intents()

    # Then
    assert len(visible) == 1
    assert visible[0].entry_limit == Decimal("10.0")


def test_writer_capability_expires_when_context_closes(tmp_path: Path) -> None:
    # Given
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.save_intent(_intent(), quantity=259)

    # When / Then
    with pytest.raises(InactiveExecutionWriterError, match="종료"):
        _ = writer.save_intent(_intent(), quantity=259)


def test_event_key_with_different_payload_is_rejected(tmp_path: Path) -> None:
    # Given
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    event = BrokerOrderEvent(
        BrokerEventKey("paper-order-1:accepted:0"),
        _intent().intent_id,
        _intent().created_at,
        BrokerOrderEventType.ACCEPTED,
        BrokerOrderId("paper-order-1"),
        '{"status":"accepted"}',
    )

    # When / Then
    with store.writer() as writer:
        _ = writer.save_intent(_intent(), quantity=259)
        assert writer.append_broker_event(event) is True
        changed = BrokerOrderEvent(
            event.event_key,
            event.intent_id,
            event.occurred_at,
            event.event_type,
            event.broker_order_id,
            '{"status":"filled"}',
        )
        with pytest.raises(BrokerEventConflictError, match="immutable"):
            _ = writer.append_broker_event(changed)


def test_database_blocks_direct_update_delete_and_orphan_event(tmp_path: Path) -> None:
    # Given
    database = tmp_path / "execution.sqlite3"
    store = ExecutionStore(database)
    with store.writer() as writer:
        _ = writer.save_intent(_intent(), quantity=259)

    # When / Then
    with sqlite3.connect(database) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute(
                "UPDATE order_intents SET quantity = 1 WHERE intent_id = ?",
                (_intent().intent_id,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute(
                "DELETE FROM order_intents WHERE intent_id = ?",
                (_intent().intent_id,),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            _ = connection.execute(
                """INSERT INTO broker_order_events
                (event_key, intent_id, occurred_at, event_type, broker_order_id, payload_json)
                VALUES ('orphan', 'missing', '2026-07-14T13:36:00+00:00',
                'accepted', 'paper-order-1', '{}')"""
            )


def test_writer_binds_exactly_one_paper_account_fingerprint(tmp_path: Path) -> None:
    # Given
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    first = AccountFingerprint("a" * 64)
    other = AccountFingerprint("b" * 64)
    bound_at = dt.datetime(2026, 7, 14, 13, 25, tzinfo=dt.UTC)

    # When / Then
    with store.writer() as writer:
        assert writer.bind_account(first, bound_at) is True
        assert writer.bind_account(first, bound_at) is False
        with pytest.raises(AccountBindingConflictError, match="다른 Alpaca paper 계좌"):
            _ = writer.bind_account(other, bound_at)
    assert store.account_fingerprint() == first
