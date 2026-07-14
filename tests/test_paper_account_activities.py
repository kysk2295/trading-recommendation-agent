from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.paper_stream_recovery_fixtures import recovery
from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
)
from trading_agent.paper_account_activity_store import (
    PaperAccountActivityConflictError,
)
from trading_agent.paper_execution_models import (
    AccountActivityId,
    BrokerOrderId,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperTradeActivity,
    PaperTradeActivityType,
)
from trading_agent.paper_stream_recovery import (
    PaperRecoveryOrderObservation,
    PaperRecoveryOrderSource,
    PaperStreamRecoveryObservation,
)


def _activity() -> PaperTradeActivity:
    return PaperTradeActivity(
        activity_id=AccountActivityId("20260714133600123::execution-1"),
        broker_order_id=BrokerOrderId("paper-order-1"),
        symbol="AAA",
        side=PaperOrderSide.BUY,
        event_type=PaperTradeActivityType.PARTIAL_FILL,
        quantity=Decimal("2"),
        cumulative_quantity=Decimal("2"),
        leaves_quantity=Decimal("98"),
        price=Decimal("10.05"),
        transaction_time=OBSERVED_AT,
        payload_json='{"activity_type":"FILL","id":"activity-1"}',
    )


def test_recovery_persists_fill_activity_as_immutable_evidence(
    tmp_path: Path,
) -> None:
    # Given: a current-epoch recovery containing one broker FILL activity.
    store = initialized_store(tmp_path)
    baseline = recovery(
        epoch="epoch-activity",
        started_at=OBSERVED_AT,
        completed_at=OBSERVED_AT + dt.timedelta(seconds=1),
    )
    observation = PaperStreamRecoveryObservation(
        account_fingerprint=baseline.account_fingerprint,
        connection_epoch=baseline.connection_epoch,
        started_at=baseline.started_at,
        completed_at=baseline.completed_at,
        snapshot_json=baseline.snapshot_json,
        execution_detail_complete=baseline.execution_detail_complete,
        orders=baseline.orders,
        activities=(_activity(),),
    )

    # When: the same recovery is safely replayed through the single writer.
    with store.writer() as writer:
        inserted = writer.append_paper_stream_recovery(observation)
        replay = writer.append_paper_stream_recovery(observation)

    # Then: one append-only activity is linked to one immutable recovery.
    stored = store.paper_account_activities()
    assert inserted is True
    assert replay is False
    assert len(stored) == 1
    assert stored[0].activity == _activity()


def test_fill_activity_repairs_missing_wss_execution_projection(
    tmp_path: Path,
) -> None:
    # Given: a filled REST order whose only execution-level evidence is Account Activities.
    store = initialized_store(tmp_path)
    stored_intent = store.intents()[0]
    order = PaperOrderSnapshot(
        broker_order_id=BrokerOrderId("paper-order-1"),
        client_order_id=stored_intent.intent_id,
        symbol=stored_intent.symbol,
        side=stored_intent.side,
        status="filled",
        quantity=Decimal(stored_intent.quantity),
        filled_quantity=Decimal(stored_intent.quantity),
        limit_price=stored_intent.entry_limit,
        time_in_force="day",
        extended_hours=False,
        filled_average_price=Decimal("10.05"),
    )
    activity = PaperTradeActivity(
        AccountActivityId("20260714133600123::execution-full"),
        order.broker_order_id,
        order.symbol,
        order.side,
        PaperTradeActivityType.FILL,
        order.quantity,
        order.quantity,
        Decimal(0),
        Decimal("10.05"),
        OBSERVED_AT,
        '{"activity_type":"FILL","id":"execution-full"}',
    )
    observation = PaperStreamRecoveryObservation(
        account_fingerprint=FINGERPRINT,
        connection_epoch="epoch-projection",
        started_at=OBSERVED_AT,
        completed_at=OBSERVED_AT + dt.timedelta(seconds=1),
        snapshot_json='{"orders":[{"status":"filled"}]}',
        execution_detail_complete=True,
        orders=(
            PaperRecoveryOrderObservation(
                PaperRecoveryOrderSource.TARGETED,
                order,
            ),
        ),
        activities=(activity,),
    )

    # When: the append-only ledger projects the order after restart.
    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(observation)
    state = store.reconciliation_ledger().order_states[0]

    # Then: the recovered activity restores exact quantity and execution price.
    assert state.cumulative_filled_quantity == order.quantity
    assert state.execution_average_price == Decimal("10.05")
    assert state.execution_detail_complete is True


def test_same_activity_id_with_changed_payload_fails_closed(
    tmp_path: Path,
) -> None:
    # Given: one activity ID was already committed by an earlier recovery epoch.
    store = initialized_store(tmp_path)
    first = recovery(
        epoch="epoch-first",
        started_at=OBSERVED_AT,
        completed_at=OBSERVED_AT + dt.timedelta(seconds=1),
    )
    first = replace(first, activities=(_activity(),))
    conflicting = replace(
        first,
        connection_epoch="epoch-conflict",
        started_at=OBSERVED_AT + dt.timedelta(seconds=2),
        completed_at=OBSERVED_AT + dt.timedelta(seconds=3),
        activities=(
            replace(
                _activity(),
                payload_json='{"activity_type":"FILL","id":"changed"}',
            ),
        ),
    )

    # When / Then: the writer rejects the correction-like mutation instead of overwriting history.
    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(first)
        with pytest.raises(PaperAccountActivityConflictError, match="immutable"):
            _ = writer.append_paper_stream_recovery(conflicting)
