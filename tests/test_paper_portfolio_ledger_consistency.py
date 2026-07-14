from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT, initialized_store
from trading_agent.broker_order_projection import BrokerOrderLedgerState
from trading_agent.paper_execution_models import (
    BrokerOrderEventType,
    BrokerOrderId,
    PaperAccountSnapshot,
    PaperBrokerState,
)
from trading_agent.paper_order_gate_models import IncompletePaperPortfolio
from trading_agent.paper_portfolio_builder import build_paper_portfolio


def test_builder_rejects_missing_position_for_a_fill_bearing_ledger_state(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    stored_intent = store.intents()[0]
    state = BrokerOrderLedgerState(
        intent_id=stored_intent.intent_id,
        broker_order_ids=(BrokerOrderId("paper-order-1"),),
        terminal_event_types=(BrokerOrderEventType.FILL,),
        cumulative_filled_quantity=Decimal(100),
        complete_fill=True,
        terminal=True,
        has_fill_evidence=True,
        anomaly_reasons=(),
    )
    broker_state = PaperBrokerState(
        PaperAccountSnapshot(
            observed_at=OBSERVED_AT,
            status="ACTIVE",
            trading_blocked=False,
            equity=Decimal(30_000),
            last_equity=Decimal(30_000),
            buying_power=Decimal(60_000),
            account_fingerprint=FINGERPRINT,
        ),
        (),
        (),
    )

    portfolio = build_paper_portfolio(
        broker_state,
        (stored_intent,),
        frozenset({stored_intent.intent_id}),
        order_states=(state,),
    )

    assert isinstance(portfolio, IncompletePaperPortfolio)
    assert any("체결 원장" in reason for reason in portfolio.reasons)
