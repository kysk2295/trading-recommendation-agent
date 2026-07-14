from __future__ import annotations

from pathlib import Path

import pytest

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    OTHER_FINGERPRINT,
    intent,
)
from trading_agent.execution_errors import (
    AccountBindingConflictError,
    UnboundExecutionAccountError,
)
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import (
    BrokerEventKey,
    BrokerOrderEvent,
    BrokerOrderEventType,
    BrokerOrderId,
)


def test_broker_event_requires_the_exact_bound_account(tmp_path: Path) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    event = BrokerOrderEvent(
        BrokerEventKey("paper-order-1:submitted:0"),
        intent().intent_id,
        intent().created_at,
        BrokerOrderEventType.SUBMITTED,
        BrokerOrderId("paper-order-1"),
        '{"status":"submitted"}',
    )
    with store.writer() as writer:
        _ = writer.save_intent(intent(), quantity=100)
        with pytest.raises(UnboundExecutionAccountError, match="결합되지"):
            _ = writer.append_broker_event(
                event,
                account_fingerprint=FINGERPRINT,
            )
        _ = writer.bind_account(FINGERPRINT, OBSERVED_AT)
        with pytest.raises(AccountBindingConflictError, match="다른 Alpaca paper 계좌"):
            _ = writer.append_broker_event(
                event,
                account_fingerprint=OTHER_FINGERPRINT,
            )
