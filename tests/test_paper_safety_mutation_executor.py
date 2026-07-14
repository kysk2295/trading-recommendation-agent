from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from tests.paper_entry_mutation_fixtures import FakeEntryMutationBroker
from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
)
from trading_agent.paper_execution_models import BrokerOrderId, PaperOrderSide
from trading_agent.paper_mutation_executor import (
    PaperMutationExecutor,
    PaperMutationExecutorDependencies,
)
from trading_agent.paper_mutation_executor_models import PaperMutationExecutionState
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)


def test_safety_cancel_stage_never_closes_before_current_epoch_reconciliation(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    safety_plan = PaperSafetyPlan(
        FINGERPRINT,
        OBSERVED_AT,
        dt.date(2026, 7, 14),
        PaperSafetyPhase.KILL_SWITCH,
        Decimal("-301"),
        Decimal("-301"),
        (
            PaperCancelOrderAction(BrokerOrderId("entry-1"), "AAA", False),
            PaperClosePositionAction("AAA", PaperOrderSide.SELL, Decimal(10)),
        ),
    )
    with store.writer() as writer:
        _ = writer.save_paper_safety_plan(safety_plan)
    broker = FakeEntryMutationBroker(store.path)

    with store.writer() as writer:
        results = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT,
            )
        ).execute_safety_plan(store.paper_safety_plans()[0])

    assert tuple(result.state for result in results) == (PaperMutationExecutionState.ACKNOWLEDGED,)
    assert broker.calls == ["cancel:entry-1"]
    assert tuple(stored.intent.operation.value for stored in store.paper_mutation_intents()) == ("cancel_order",)


def test_safety_close_only_stage_executes_exact_position_close(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    safety_plan = PaperSafetyPlan(
        FINGERPRINT,
        OBSERVED_AT,
        dt.date(2026, 7, 14),
        PaperSafetyPhase.EOD_FLATTEN,
        Decimal("-5"),
        Decimal("-7.5"),
        (PaperClosePositionAction("AAA", PaperOrderSide.SELL, Decimal(10)),),
    )
    with store.writer() as writer:
        _ = writer.save_paper_safety_plan(safety_plan)
    broker = FakeEntryMutationBroker(store.path)

    with store.writer() as writer:
        results = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT,
            )
        ).execute_safety_plan(store.paper_safety_plans()[0])

    assert tuple(result.state for result in results) == (PaperMutationExecutionState.ACKNOWLEDGED,)
    assert broker.calls == ["close:AAA"]
    assert tuple(stored.intent.operation.value for stored in store.paper_mutation_intents()) == ("close_position",)
