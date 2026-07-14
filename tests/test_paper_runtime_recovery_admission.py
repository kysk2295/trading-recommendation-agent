from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.paper_runtime_fixtures import (
    FakeLedgerReader,
    FakeReadyStream,
    account,
    candidate,
    credentials,
    latest_bar,
    ledger,
    market_clock,
    stream_opener,
)
from tests.trade_update_ledger_fixtures import OBSERVED_AT, initialized_store
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    PaperBrokerState,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_order_gate_models import PaperOrderGateState
from trading_agent.paper_runtime_session import (
    _open_paper_runtime_session,
    _probe_paper_runtime,
)
from trading_agent.paper_safety_models import PaperSafetyPhase, PaperSafetyPlan
from trading_agent.paper_safety_store import (
    PaperSafetyPlanKey,
    StoredPaperSafetyPlan,
)
from trading_agent.paper_stream_recovery import (
    PaperRecoveryOrderObservation,
    PaperRecoveryOrderSource,
    PaperStreamRecoveryObservation,
)
from trading_agent.trade_update_receipts import TradeUpdateReceiptKey


@pytest.mark.parametrize(
    "ledger_field",
    (
        "pending_trade_update_receipt_keys",
        "unrecovered_trade_update_quarantine_keys",
    ),
)
def test_runtime_blocks_unclassified_or_unrecovered_raw_receipts(
    ledger_field: str,
) -> None:
    stream = FakeReadyStream()
    unsafe_ledger = replace(
        ledger(),
        **{ledger_field: frozenset({TradeUpdateReceiptKey("receipt-1")})},
    )

    readiness = _probe_paper_runtime(
        credentials(),
        FakeLedgerReader(stream, unsafe_ledger),
        state_loader=lambda _: (PaperBrokerState(account(), (), ()), market_clock()),
        stream_opener=stream_opener(stream),
    )

    assert readiness.ready is False
    assert any("trade update raw receipt" in reason for reason in readiness.reasons)


def test_runtime_blocks_new_entry_after_rest_fill_until_protective_oco_exists(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    stored_intent = store.intents()[0]
    recovered_order = PaperOrderSnapshot(
        BrokerOrderId("paper-order-1"),
        stored_intent.intent_id,
        stored_intent.symbol,
        PaperOrderSide.BUY,
        "filled",
        Decimal(stored_intent.quantity),
        Decimal(stored_intent.quantity),
        stored_intent.entry_limit,
        "day",
        False,
        filled_average_price=Decimal("10.05"),
        updated_at=OBSERVED_AT,
        filled_at=OBSERVED_AT,
    )
    with store.writer() as writer:
        _ = writer.append_paper_stream_recovery(
            PaperStreamRecoveryObservation(
                account_fingerprint=account().account_fingerprint,
                connection_epoch="recovered-epoch",
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
        )
    stream = FakeReadyStream()
    broker_state = PaperBrokerState(
        account(),
        (),
        (
            PaperPositionSnapshot(
                stored_intent.symbol,
                Decimal(stored_intent.quantity),
                Decimal("1005"),
            ),
        ),
    )

    with _open_paper_runtime_session(
        credentials(),
        store,
        state_loader=lambda _: (broker_state, market_clock()),
        stream_opener=stream_opener(stream),
        _clock=lambda: dt.datetime(2026, 7, 14, 13, 36, 3, tzinfo=dt.UTC),
    ) as session:
        decision = session.evaluate_order(
            latest_bar=latest_bar(),
            candidate_intent=candidate(),
            liquidity_allowed_quantity=1_000,
            estimated_spread_bps=0.0,
        )

    state = store.reconciliation_ledger().order_states[0]
    assert decision.state is PaperOrderGateState.PORTFOLIO_BLOCKED
    assert "보호 OCO" in " ".join(decision.reasons)
    assert state.execution_detail_complete is False
    assert state.warning_reasons


def test_runtime_keeps_new_entries_blocked_after_daily_kill_is_latched() -> None:
    stream = FakeReadyStream()
    kill_plan = PaperSafetyPlan(
        account().account_fingerprint,
        OBSERVED_AT,
        dt.date(2026, 7, 14),
        PaperSafetyPhase.KILL_SWITCH,
        Decimal("-301"),
        Decimal("-301"),
        (),
    )
    killed_ledger = replace(
        ledger(),
        paper_safety_plans=(
            StoredPaperSafetyPlan(
                PaperSafetyPlanKey("k" * 64),
                "h" * 64,
                kill_plan,
            ),
        ),
    )

    with _open_paper_runtime_session(
        credentials(),
        FakeLedgerReader(stream, killed_ledger),
        state_loader=lambda _: (PaperBrokerState(account(), (), ()), market_clock()),
        stream_opener=stream_opener(stream),
        _clock=lambda: dt.datetime(2026, 7, 14, 13, 36, 3, tzinfo=dt.UTC),
    ) as session:
        decision = session.evaluate_order(
            latest_bar=latest_bar(),
            candidate_intent=candidate(),
            liquidity_allowed_quantity=1_000,
            estimated_spread_bps=0.0,
        )

    assert decision.state is PaperOrderGateState.RECONCILIATION_BLOCKED
    assert "kill switch" in " ".join(decision.reasons)
