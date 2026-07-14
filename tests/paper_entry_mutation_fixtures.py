from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx2

from tests.test_paper_mutation_executor import FakeMutationBroker
from tests.trade_update_ledger_fixtures import OBSERVED_AT, intent
from trading_agent.alpaca_paper_mutation_client import PaperMutationRejectedError
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import BrokerOrderId, PaperOrderSnapshot, SizedPaperOrder
from trading_agent.paper_mutation_models import PaperEntryOrderReceipt, PaperMutationRequestId


def entry_order() -> SizedPaperOrder:
    return SizedPaperOrder(intent(), 100, 0.25, 25.0, 1000.0)


class FakeEntryMutationBroker(FakeMutationBroker):
    def __init__(self, store_path: Path) -> None:
        super().__init__(store_path)
        self.entry_failure: httpx2.TransportError | PaperMutationRejectedError | None = None

    def submit_entry(self, order: SizedPaperOrder) -> PaperEntryOrderReceipt:
        events = ExecutionStore(self.store_path).paper_mutation_events()
        assert events[-1].event.event_type.value == "attempted"
        self.calls.append(f"entry:{order.intent.symbol}")
        if self.entry_failure is not None:
            raise self.entry_failure
        snapshot = PaperOrderSnapshot(
            BrokerOrderId("entry-1"),
            order.intent.intent_id,
            order.intent.symbol,
            order.intent.side,
            "accepted",
            Decimal(order.quantity),
            Decimal(0),
            Decimal(str(order.intent.entry_limit)),
            "day",
            False,
        )
        return PaperEntryOrderReceipt(
            PaperMutationRequestId("request-entry-1"),
            OBSERVED_AT,
            snapshot,
        )
