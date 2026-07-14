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
    trade_update,
)
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperOrderSide,
    PaperOrderSnapshot,
)
from trading_agent.paper_stream_recovery import (
    PaperRecoveryOrderObservation,
    PaperRecoveryOrderSource,
    PaperStreamRecoveryObservation,
)
from trading_agent.paper_stream_recovery_runtime import PaperRecoveryState
from trading_agent.paper_stream_recovery_snapshot import execution_details_are_complete


def test_rest_aggregate_only_fill_stays_execution_detail_incomplete(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    account = PaperAccountSnapshot(
        observed_at=OBSERVED_AT + dt.timedelta(seconds=2),
        status="ACTIVE",
        trading_blocked=False,
        equity=Decimal(30_000),
        last_equity=Decimal(30_000),
        buying_power=Decimal(60_000),
        account_fingerprint=FINGERPRINT,
    )
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
    )
    first = PaperStreamRecoveryObservation(
        FINGERPRINT,
        "epoch-detail-1",
        OBSERVED_AT,
        OBSERVED_AT + dt.timedelta(seconds=1),
        '{"filled_qty":"100"}',
        False,
        (
            PaperRecoveryOrderObservation(
                PaperRecoveryOrderSource.TARGETED,
                recovered_order,
            ),
        ),
    )
    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(first)

    ledger = store.reconciliation_ledger()
    recovery_state = PaperRecoveryState(
        PaperBrokerState(account, (), ()),
        (recovered_order,),
    )
    detail_complete = execution_details_are_complete(recovery_state, ledger)
    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(
            replace(
                first,
                connection_epoch="epoch-detail-2",
                started_at=OBSERVED_AT + dt.timedelta(seconds=2),
                completed_at=OBSERVED_AT + dt.timedelta(seconds=3),
                execution_detail_complete=detail_complete,
            )
        )

    assert detail_complete is False
    assert store.paper_stream_recoveries()[-1].execution_detail_complete is False


def test_rest_average_price_must_match_execution_details(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_trade_update(
            trade_update(
                event="fill",
                status="filled",
                filled_qty="100",
                execution_qty="100",
            ),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-fill",
            received_at=OBSERVED_AT,
        )
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
        filled_average_price=Decimal("10.50"),
    )
    account = PaperAccountSnapshot(
        observed_at=OBSERVED_AT,
        status="ACTIVE",
        trading_blocked=False,
        equity=Decimal(30_000),
        last_equity=Decimal(30_000),
        buying_power=Decimal(60_000),
        account_fingerprint=FINGERPRINT,
    )
    recovery_state = PaperRecoveryState(
        PaperBrokerState(account, (), ()),
        (recovered_order,),
    )

    assert execution_details_are_complete(
        recovery_state,
        store.reconciliation_ledger(),
    ) is False


@pytest.mark.parametrize(
    ("status", "terminal"),
    (
        ("canceled", True),
        ("expired", True),
        ("rejected", True),
        ("done_for_day", False),
        ("held", False),
    ),
)
def test_rest_recovery_uses_the_documented_terminal_status_policy(
    tmp_path: Path,
    status: str,
    terminal: bool,
) -> None:
    store = initialized_store(tmp_path)
    recovered_order = PaperOrderSnapshot(
        BrokerOrderId("paper-order-1"),
        store.intents()[0].intent_id,
        "AAA",
        PaperOrderSide.BUY,
        status,
        Decimal(100),
        Decimal(0),
        Decimal(10),
        "day",
        False,
        updated_at=OBSERVED_AT,
    )
    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(
            PaperStreamRecoveryObservation(
                account_fingerprint=FINGERPRINT,
                connection_epoch="epoch-status",
                started_at=OBSERVED_AT,
                completed_at=OBSERVED_AT + dt.timedelta(seconds=1),
                snapshot_json=f'{{"status":"{status}"}}',
                execution_detail_complete=True,
                orders=(
                    PaperRecoveryOrderObservation(
                        PaperRecoveryOrderSource.TARGETED,
                        recovered_order,
                    ),
                ),
            )
        )

    ledger = store.reconciliation_ledger()
    assert ledger.order_states[0].terminal is terminal
    assert (store.intents()[0].intent_id in ledger.unresolved_intent_ids) is not terminal
