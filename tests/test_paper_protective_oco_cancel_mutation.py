from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest

from tests.paper_stream_recovery_fixtures import recovery
from tests.test_paper_mutation_executor import (
    FakeMutationBroker,
    _oco_snapshot,
    _protective_plan,
)
from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT, initialized_store
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import BrokerOrderId, IntentId
from trading_agent.paper_mutation_executor import (
    PaperMutationExecutor,
    PaperMutationExecutorDependencies,
)
from trading_agent.paper_mutation_executor_models import PaperMutationExecutionState
from trading_agent.paper_mutation_intents import protective_oco_cancel_mutation_intent
from trading_agent.paper_mutation_ledger_models import PaperMutationOperation
from trading_agent.paper_mutation_validation import InvalidPaperMutationRecordError
from trading_agent.paper_protective_oco_lifecycle import ProtectiveOcoResizeCancelPlan


def test_protective_cancel_is_source_bound_and_never_redeletes_ack(
    tmp_path: Path,
) -> None:
    store, decision = _store_and_decision(tmp_path)
    broker = FakeMutationBroker(store.path)

    with store.writer() as writer:
        executor = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT + dt.timedelta(seconds=2),
            )
        )
        source = store.protective_oco_plans()[0]
        first = executor.execute_protective_oco_cancel(FINGERPRINT, source, decision)
        replay = executor.execute_protective_oco_cancel(FINGERPRINT, source, decision)

    assert first.state is PaperMutationExecutionState.ACKNOWLEDGED
    assert replay.state is PaperMutationExecutionState.ALREADY_ACKNOWLEDGED
    assert broker.calls == ["cancel:oco-parent-1"]
    stored = store.paper_mutation_intents()[0].intent
    assert stored.operation is PaperMutationOperation.CANCEL_PROTECTIVE_OCO
    assert stored.protective_plan_key == decision.source_plan_key
    assert stored.broker_order_id == decision.broker_order_id
    assert stored.safety_plan_key is None


def test_protective_cancel_rejects_a_broker_order_not_in_recovery(
    tmp_path: Path,
) -> None:
    store, decision = _store_and_decision(tmp_path)
    broker = FakeMutationBroker(store.path)
    wrong = replace(decision, broker_order_id=BrokerOrderId("unobserved-oco"))

    with store.writer() as writer:
        executor = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT + dt.timedelta(seconds=2),
            )
        )
        with pytest.raises(InvalidPaperMutationRecordError):
            _ = executor.execute_protective_oco_cancel(
                FINGERPRINT,
                store.protective_oco_plans()[0],
                wrong,
            )

    assert broker.calls == []
    assert store.paper_mutation_intents() == ()


def test_protective_cancel_rejects_a_mismatched_parent_intent(
    tmp_path: Path,
) -> None:
    store, decision = _store_and_decision(tmp_path)
    broker = FakeMutationBroker(store.path)
    wrong = replace(decision, parent_intent_id=IntentId("different-entry"))

    with store.writer() as writer:
        executor = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT + dt.timedelta(seconds=2),
            )
        )
        with pytest.raises(InvalidPaperMutationRecordError):
            _ = executor.execute_protective_oco_cancel(
                FINGERPRINT,
                store.protective_oco_plans()[0],
                wrong,
            )

    assert broker.calls == []
    assert store.paper_mutation_intents() == ()


@pytest.mark.parametrize(
    "wrong",
    (
        lambda decision: replace(decision, source_plan_key="0" * 64),
        lambda decision: replace(decision, symbol="BBB"),
    ),
)
def test_protective_cancel_rejects_a_mismatched_source_plan(
    tmp_path: Path,
    wrong: Callable[
        [ProtectiveOcoResizeCancelPlan],
        ProtectiveOcoResizeCancelPlan,
    ],
) -> None:
    store, decision = _store_and_decision(tmp_path)

    with pytest.raises(InvalidPaperMutationRecordError):
        _ = protective_oco_cancel_mutation_intent(
            FINGERPRINT,
            store.protective_oco_plans()[0],
            wrong(decision),
        )


def test_protective_cancel_timeout_is_ambiguous_and_never_redeleted(
    tmp_path: Path,
) -> None:
    store, decision = _store_and_decision(tmp_path)
    broker = FakeMutationBroker(store.path)
    broker.cancel_failure = httpx2.ReadTimeout("timeout")

    with store.writer() as writer:
        executor = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT + dt.timedelta(seconds=2),
            )
        )
        source = store.protective_oco_plans()[0]
        first = executor.execute_protective_oco_cancel(FINGERPRINT, source, decision)
        replay = executor.execute_protective_oco_cancel(FINGERPRINT, source, decision)

    assert first.state is PaperMutationExecutionState.AMBIGUOUS
    assert replay.state is PaperMutationExecutionState.AMBIGUOUS
    assert broker.calls == ["cancel:oco-parent-1"]


def _store_and_decision(
    tmp_path: Path,
) -> tuple[ExecutionStore, ProtectiveOcoResizeCancelPlan]:
    store = initialized_store(tmp_path)
    plan = _protective_plan()
    snapshot = _oco_snapshot()
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(plan, OBSERVED_AT)
        _ = writer.append_paper_stream_recovery(
            replace(
                recovery(
                    epoch="epoch-protective-cancel",
                    started_at=OBSERVED_AT - dt.timedelta(seconds=1),
                    completed_at=OBSERVED_AT + dt.timedelta(seconds=1),
                ),
                protective_ocos=(snapshot,),
            )
        )
    source = store.protective_oco_plans()[0]
    return store, ProtectiveOcoResizeCancelPlan(
        source.plan.parent_intent_id,
        source.plan_key,
        snapshot.take_profit.broker_order_id,
        source.plan.symbol,
        snapshot.observed_at,
    )
