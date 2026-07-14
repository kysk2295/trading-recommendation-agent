from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from tests.test_paper_mutation_executor import FakeMutationBroker
from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT, initialized_store
from trading_agent.paper_execution_models import BrokerOrderId
from trading_agent.paper_mutation_executor import (
    PaperMutationExecutor,
    PaperMutationExecutorDependencies,
)
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)
from trading_agent.paper_safety_mutation_guard import (
    repeated_acknowledged_safety_action_reasons,
)


def _plan(observed_at: dt.datetime) -> PaperSafetyPlan:
    return PaperSafetyPlan(
        FINGERPRINT,
        observed_at,
        dt.date(2026, 7, 14),
        PaperSafetyPhase.ENTRY_CUTOFF,
        Decimal(0),
        Decimal(0),
        (PaperCancelOrderAction(BrokerOrderId("entry-1"), "AAA", False),),
    )


def test_new_snapshot_cannot_repeat_an_acknowledged_same_day_safety_request(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_paper_safety_plan(_plan(OBSERVED_AT))
        _ = writer.save_paper_safety_plan(_plan(OBSERVED_AT + dt.timedelta(seconds=1)))
        _ = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                FakeMutationBroker(store.path),
                lambda: OBSERVED_AT,
            )
        ).execute_safety_plan(store.paper_safety_plans()[0])

    reasons = repeated_acknowledged_safety_action_reasons(
        store.paper_safety_plans()[1],
        store.paper_safety_plans(),
        store.paper_mutation_intents(),
        store.paper_mutation_events(),
    )

    assert "재실행" in reasons[0]
