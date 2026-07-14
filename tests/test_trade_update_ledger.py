from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    OTHER_FINGERPRINT,
    initialized_store,
    intent,
    trade_update,
)
from trading_agent.execution_errors import (
    AccountBindingConflictError,
    TradeUpdateConflictError,
    TradeUpdateOrderMismatchError,
    UnboundExecutionAccountError,
    UnexpectedBrokerOrderIdError,
    UnknownTradeUpdateIntentError,
)
from trading_agent.execution_store import ExecutionStore


def test_partial_fill_is_idempotent_across_reconnect_and_restart(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    update = trade_update()

    with store.writer() as writer:
        first = writer.append_trade_update(
            update,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )
    restarted = ExecutionStore(store.path)
    with restarted.writer() as writer:
        replayed = writer.append_trade_update(
            update,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-2",
            received_at=OBSERVED_AT + dt.timedelta(minutes=1),
        )

    stored = restarted.trade_updates(intent().intent_id)
    ledger = restarted.reconciliation_ledger()
    assert first is True
    assert replayed is False
    assert len(stored) == 1
    assert stored[0].connection_epoch == "epoch-1"
    assert stored[0].execution_quantity == Decimal("10")
    assert stored[0].execution_price == Decimal("10.05")
    assert stored[0].cumulative_filled_quantity == Decimal("10")
    assert ledger.unresolved_intent_ids == frozenset({intent().intent_id})
    assert ledger.filled_intent_ids == frozenset({intent().intent_id})
    state = ledger.order_states[0]
    assert state.cumulative_filled_quantity == Decimal("10")
    assert state.complete_fill is False
    assert state.anomaly_reasons == ()


def test_same_trade_update_key_with_changed_execution_is_rejected(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    update = trade_update()

    with store.writer() as writer:
        assert writer.append_trade_update(
            update,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        ) is True
        changed = replace(
            update,
            execution_price=Decimal("10.06"),
            payload_json='{"changed":true}',
        )
        with pytest.raises(TradeUpdateConflictError, match="immutable"):
            _ = writer.append_trade_update(
                changed,
                account_fingerprint=FINGERPRINT,
                connection_epoch="epoch-1",
                received_at=OBSERVED_AT,
            )


def test_trade_update_requires_matching_bound_account(tmp_path: Path) -> None:
    unbound = ExecutionStore(tmp_path / "unbound.sqlite3")
    with unbound.writer() as writer:
        _ = writer.save_intent(intent(), quantity=100)
        with pytest.raises(UnboundExecutionAccountError, match="결합"):
            _ = writer.append_trade_update(
                trade_update(),
                account_fingerprint=FINGERPRINT,
                connection_epoch="epoch-1",
                received_at=OBSERVED_AT,
            )

    bound = initialized_store(tmp_path / "bound")
    with (
        bound.writer() as writer,
        pytest.raises(AccountBindingConflictError, match="다른 Alpaca paper 계좌"),
    ):
        _ = writer.append_trade_update(
            trade_update(),
            account_fingerprint=OTHER_FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )


def test_trade_update_for_unknown_intent_fails_closed(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.bind_account(FINGERPRINT, OBSERVED_AT)
        with pytest.raises(UnknownTradeUpdateIntentError, match="intent"):
            _ = writer.append_trade_update(
                trade_update(),
                account_fingerprint=FINGERPRINT,
                connection_epoch="epoch-1",
                received_at=OBSERVED_AT,
            )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("symbol", "BBB"),
        ("side", "sell"),
        ("order_qty", "99"),
        ("limit_price", "10.10"),
        ("time_in_force", "gtc"),
        ("extended_hours", True),
    ),
)
def test_trade_update_must_match_the_stored_intent(
    tmp_path: Path,
    field: str,
    value: str | bool,
) -> None:
    store = initialized_store(tmp_path)
    update = trade_update(**{field: value})

    with store.writer() as writer, pytest.raises(
        TradeUpdateOrderMismatchError,
        match="불일치",
    ):
        _ = writer.append_trade_update(
            update,
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )


def test_unlinked_second_broker_order_id_fails_closed(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_trade_update(
            trade_update(),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )
        with pytest.raises(UnexpectedBrokerOrderIdError, match="order ID"):
            _ = writer.append_trade_update(
                trade_update(
                    "accepted",
                    status="accepted",
                    filled_qty="0",
                    execution_id=None,
                    order_id="paper-order-2",
                ),
                account_fingerprint=FINGERPRINT,
                connection_epoch="epoch-1",
                received_at=OBSERVED_AT,
            )


def test_linked_replacement_with_changed_price_is_preserved_and_blocked(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_trade_update(
            trade_update(),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )
        inserted = writer.append_trade_update(
            trade_update(
                "accepted",
                status="accepted",
                filled_qty="0",
                execution_id=None,
                order_id="paper-order-2",
                order_qty="80",
                limit_price="10.10",
                replaces="paper-order-1",
            ),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-1",
            received_at=OBSERVED_AT,
        )

    state = store.reconciliation_ledger().order_states[0]
    assert inserted is True
    assert len(store.trade_updates(intent().intent_id)) == 2
    assert state.broker_order_ids == ("paper-order-1", "paper-order-2")
    assert any("교체 주문" in reason for reason in state.anomaly_reasons)
