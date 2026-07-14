from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
)
from trading_agent.alpaca_paper_order_stream import (
    PaperOrderStreamHeartbeat,
    PaperStreamEpoch,
)
from trading_agent.paper_execution_models import (
    AccountActivityId,
    BrokerOrderId,
    IntentId,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperTradeActivity,
    PaperTradeActivityType,
)
from trading_agent.paper_stream_recovery_runtime import (
    PaperRecoveryState,
    PaperStreamRecoveryIncompleteError,
    build_paper_stream_recovery_observation,
)


def _account() -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        observed_at=OBSERVED_AT,
        status="ACTIVE",
        trading_blocked=False,
        equity=Decimal(30_000),
        last_equity=Decimal(30_000),
        buying_power=Decimal(60_000),
        account_fingerprint=FINGERPRINT,
    )


def _heartbeats() -> tuple[PaperOrderStreamHeartbeat, PaperOrderStreamHeartbeat]:
    before = PaperOrderStreamHeartbeat(
        PaperStreamEpoch("epoch-1"),
        OBSERVED_AT - dt.timedelta(seconds=2),
        OBSERVED_AT - dt.timedelta(seconds=2),
        OBSERVED_AT - dt.timedelta(seconds=1),
    )
    return before, replace(before, pong_at=OBSERVED_AT + dt.timedelta(seconds=1))


def _known_order(intent_id: IntentId) -> PaperOrderSnapshot:
    return PaperOrderSnapshot(
        BrokerOrderId("paper-order-1"),
        intent_id,
        "AAA",
        PaperOrderSide.BUY,
        "accepted",
        Decimal(100),
        Decimal(0),
        Decimal(10),
        "day",
        False,
    )


def test_recent_order_unknown_to_the_local_ledger_blocks_recovery(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    known = _known_order(store.intents()[0].intent_id)
    unknown = PaperOrderSnapshot(
        BrokerOrderId("paper-order-unknown"),
        IntentId("unknown-intent"),
        "BBB",
        PaperOrderSide.BUY,
        "filled",
        Decimal(10),
        Decimal(10),
        Decimal(5),
        "day",
        False,
    )
    before, after = _heartbeats()

    with pytest.raises(PaperStreamRecoveryIncompleteError, match="알 수 없는 recent"):
        _ = build_paper_stream_recovery_observation(
            before,
            after,
            PaperRecoveryState(PaperBrokerState(_account(), (), ()), (known,), (unknown,)),
            store.reconciliation_ledger(),
        )


def test_open_order_unknown_to_the_local_ledger_blocks_recovery(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    known = _known_order(store.intents()[0].intent_id)
    unknown = replace(
        known,
        broker_order_id=BrokerOrderId("paper-order-unknown"),
        client_order_id=IntentId("unknown-intent"),
        symbol="BBB",
    )
    before, after = _heartbeats()

    with pytest.raises(PaperStreamRecoveryIncompleteError, match="알 수 없는 open"):
        _ = build_paper_stream_recovery_observation(
            before,
            after,
            PaperRecoveryState(PaperBrokerState(_account(), (unknown,), ()), (known,)),
            store.reconciliation_ledger(),
        )


def test_fill_activity_for_unknown_broker_order_blocks_recovery(
    tmp_path: Path,
) -> None:
    # Given: a broker activity that cannot be joined to any PIT REST order.
    store = initialized_store(tmp_path)
    known = _known_order(store.intents()[0].intent_id)
    unknown_activity = PaperTradeActivity(
        AccountActivityId("20260714133600123::unknown"),
        BrokerOrderId("paper-order-unknown"),
        "BBB",
        PaperOrderSide.BUY,
        PaperTradeActivityType.FILL,
        Decimal(1),
        Decimal(1),
        Decimal(0),
        Decimal(5),
        OBSERVED_AT,
        '{"activity_type":"FILL","id":"unknown"}',
    )
    before, after = _heartbeats()

    # When / Then: recovery refuses to infer an owner for the execution.
    with pytest.raises(
        PaperStreamRecoveryIncompleteError,
        match="알 수 없는 Account Activity",
    ):
        _ = build_paper_stream_recovery_observation(
            before,
            after,
            PaperRecoveryState(
                PaperBrokerState(_account(), (), ()),
                (known,),
                activities=(unknown_activity,),
            ),
            store.reconciliation_ledger(),
        )
