from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
)
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    PaperOrderSide,
    PaperOrderSnapshot,
)
from trading_agent.paper_stream_recovery import (
    PaperRecoveryOrderObservation,
    PaperRecoveryOrderSource,
    PaperStreamRecoveryObservation,
)


def test_rest_terminal_fill_recovers_aggregate_state_without_synthetic_execution(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    recovered_order = PaperOrderSnapshot(
        BrokerOrderId("paper-order-1"),
        store.intents()[0].intent_id,
        "AAA",
        PaperOrderSide.BUY,
        "filled",
        Decimal(100),
        Decimal(100),
        Decimal(10),
        "day",
        False,
        filled_average_price=Decimal("10.05"),
        updated_at=OBSERVED_AT,
        filled_at=OBSERVED_AT,
    )
    observation = PaperStreamRecoveryObservation(
        account_fingerprint=FINGERPRINT,
        connection_epoch="epoch-2",
        started_at=OBSERVED_AT,
        completed_at=OBSERVED_AT + dt.timedelta(seconds=1),
        snapshot_json='{"orders":[{"status":"filled"}]}',
        execution_detail_complete=False,
        orders=(
            PaperRecoveryOrderObservation(
                PaperRecoveryOrderSource.TARGETED,
                recovered_order,
            ),
        ),
    )

    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(observation)

    ledger = store.reconciliation_ledger()
    state = ledger.order_states[0]
    assert store.trade_updates(store.intents()[0].intent_id) == ()
    assert state.terminal is True
    assert state.complete_fill is True
    assert state.cumulative_filled_quantity == Decimal(100)
    assert state.has_fill_evidence is True
    assert state.execution_detail_complete is False
    assert state.anomaly_reasons == ()
    assert any("개별 execution" in reason for reason in state.warning_reasons)
    assert ledger.unresolved_intent_ids == frozenset()
    with sqlite3.connect(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute(
                "UPDATE paper_recovery_orders SET status = 'accepted'"
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            _ = connection.execute("DELETE FROM paper_recovery_orders")


def test_rest_filled_status_with_partial_quantity_is_an_anomaly(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    recovered_order = PaperOrderSnapshot(
        BrokerOrderId("paper-order-1"),
        store.intents()[0].intent_id,
        "AAA",
        PaperOrderSide.BUY,
        "filled",
        Decimal(100),
        Decimal(50),
        Decimal(10),
        "day",
        False,
        filled_average_price=Decimal("10.05"),
    )
    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(
            PaperStreamRecoveryObservation(
                FINGERPRINT,
                "epoch-partial-filled",
                OBSERVED_AT,
                OBSERVED_AT + dt.timedelta(seconds=1),
                '{"status":"filled","filled_qty":"50"}',
                False,
                (
                    PaperRecoveryOrderObservation(
                        PaperRecoveryOrderSource.TARGETED,
                        recovered_order,
                    ),
                ),
            )
        )

    state = store.reconciliation_ledger().order_states[0]
    assert any("filled 상태" in reason for reason in state.anomaly_reasons)


def test_rest_terminal_status_cannot_change_in_a_later_recovery(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    base = PaperOrderSnapshot(
        BrokerOrderId("paper-order-1"),
        store.intents()[0].intent_id,
        "AAA",
        PaperOrderSide.BUY,
        "filled",
        Decimal(100),
        Decimal(100),
        Decimal(10),
        "day",
        False,
        filled_average_price=Decimal("10.05"),
    )
    with store.writer() as writer:
        for offset, recovered_order in enumerate(
            (base, replace(base, status="canceled")),
            start=1,
        ):
            _ = writer.append_paper_stream_recovery(
                PaperStreamRecoveryObservation(
                    FINGERPRINT,
                    f"epoch-terminal-{offset}",
                    OBSERVED_AT + dt.timedelta(seconds=offset * 2),
                    OBSERVED_AT + dt.timedelta(seconds=offset * 2 + 1),
                    f'{{"status":"{recovered_order.status}"}}',
                    False,
                    (
                        PaperRecoveryOrderObservation(
                            PaperRecoveryOrderSource.TARGETED,
                            recovered_order,
                        ),
                    ),
                )
            )

    state = store.reconciliation_ledger().order_states[0]
    assert any("종료 상태" in reason for reason in state.anomaly_reasons)


def test_rest_terminal_quantity_cannot_change_in_a_later_recovery(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    base = PaperOrderSnapshot(
        BrokerOrderId("paper-order-1"),
        store.intents()[0].intent_id,
        "AAA",
        PaperOrderSide.BUY,
        "canceled",
        Decimal(100),
        Decimal(0),
        Decimal(10),
        "day",
        False,
    )
    later = replace(
        base,
        filled_quantity=Decimal(10),
        filled_average_price=Decimal("10.05"),
    )
    with store.writer() as writer:
        for offset, recovered_order in enumerate((base, later), start=1):
            _ = writer.append_paper_stream_recovery(
                PaperStreamRecoveryObservation(
                    FINGERPRINT,
                    f"epoch-terminal-quantity-{offset}",
                    OBSERVED_AT + dt.timedelta(seconds=offset * 2),
                    OBSERVED_AT + dt.timedelta(seconds=offset * 2 + 1),
                    f'{{"filled_qty":"{recovered_order.filled_quantity}"}}',
                    False,
                    (
                        PaperRecoveryOrderObservation(
                            PaperRecoveryOrderSource.TARGETED,
                            recovered_order,
                        ),
                    ),
                )
            )

    state = store.reconciliation_ledger().order_states[0]
    assert any("종료 상태의 누적 체결 수량" in reason for reason in state.anomaly_reasons)
