from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import httpx2

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
    intent,
)
from trading_agent.alpaca_paper_mutation_client import PaperMutationRejectedError
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    PaperOrderSide,
    PaperOrderSnapshot,
)
from trading_agent.paper_mutation_executor import (
    PaperMutationExecutor,
    PaperMutationExecutorDependencies,
)
from trading_agent.paper_mutation_executor_models import PaperMutationExecutionState
from trading_agent.paper_mutation_models import (
    PaperCancelOrderReceipt,
    PaperClosePositionReceipt,
    PaperMutationRequestId,
    PaperProtectiveOcoReceipt,
)
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoClientOrderId,
    ProtectiveOcoExitPlan,
    ProtectiveOcoLegKind,
    ProtectiveOcoLegSnapshot,
    ProtectiveOcoOrderType,
    ProtectiveOcoSnapshot,
)
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
)


def _protective_plan() -> ProtectiveOcoExitPlan:
    return ProtectiveOcoExitPlan(
        ProtectiveOcoClientOrderId("protect-" + "a" * 40),
        intent().intent_id,
        "AAA",
        PaperOrderSide.SELL,
        10,
        Decimal("10.5"),
        Decimal("9.75"),
    )


def _oco_snapshot() -> ProtectiveOcoSnapshot:
    take_profit = ProtectiveOcoLegSnapshot(
        ProtectiveOcoLegKind.TAKE_PROFIT,
        BrokerOrderId("oco-parent-1"),
        _protective_plan().client_order_id,
        "AAA",
        PaperOrderSide.SELL,
        "new",
        Decimal(10),
        Decimal(0),
        ProtectiveOcoOrderType.LIMIT,
        Decimal("10.5"),
        None,
        "day",
        False,
    )
    stop = ProtectiveOcoLegSnapshot(
        ProtectiveOcoLegKind.STOP_LOSS,
        BrokerOrderId("stop-1"),
        "stop-client-1",
        "AAA",
        PaperOrderSide.SELL,
        "new",
        Decimal(10),
        Decimal(0),
        ProtectiveOcoOrderType.STOP,
        None,
        Decimal("9.75"),
        "day",
        False,
    )
    return ProtectiveOcoSnapshot(OBSERVED_AT, take_profit, stop)


class FakeMutationBroker:
    def __init__(self, store_path: Path) -> None:
        self.store_path = store_path
        self.calls: list[str] = []
        self.oco_failure: httpx2.TransportError | PaperMutationRejectedError | None = None

    def submit_protective_oco(
        self,
        plan: ProtectiveOcoExitPlan,
    ) -> PaperProtectiveOcoReceipt:
        from trading_agent.execution_store import ExecutionStore

        events = ExecutionStore(self.store_path).paper_mutation_events()
        assert events[-1].event.event_type.value == "attempted"
        self.calls.append(f"oco:{plan.symbol}")
        if self.oco_failure is not None:
            raise self.oco_failure
        return PaperProtectiveOcoReceipt(
            PaperMutationRequestId("request-oco-1"),
            _oco_snapshot(),
        )

    def cancel_order(
        self,
        action: PaperCancelOrderAction,
    ) -> PaperCancelOrderReceipt:
        self.calls.append(f"cancel:{action.broker_order_id}")
        return PaperCancelOrderReceipt(
            PaperMutationRequestId("request-cancel-1"),
            action.broker_order_id,
            OBSERVED_AT,
        )

    def close_position(
        self,
        action: PaperClosePositionAction,
    ) -> PaperClosePositionReceipt:
        self.calls.append(f"close:{action.symbol}")
        order = PaperOrderSnapshot(
            BrokerOrderId("close-1"),
            intent().intent_id,
            action.symbol,
            action.side,
            "accepted",
            action.quantity,
            Decimal(0),
            None,
            "day",
            False,
        )
        return PaperClosePositionReceipt(
            PaperMutationRequestId("request-close-1"),
            OBSERVED_AT,
            order,
        )


def test_executor_persists_attempt_before_oco_and_never_reposts_ack(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_protective_plan(), OBSERVED_AT)
    stored_plan = store.protective_oco_plans()[0]
    broker = FakeMutationBroker(store.path)

    with store.writer() as writer:
        executor = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT,
            )
        )
        first = executor.execute_protective_oco(FINGERPRINT, stored_plan)
        replay = executor.execute_protective_oco(FINGERPRINT, stored_plan)

    assert first.state is PaperMutationExecutionState.ACKNOWLEDGED
    assert replay.state is PaperMutationExecutionState.ALREADY_ACKNOWLEDGED
    assert broker.calls == ["oco:AAA"]
    assert tuple(stored.event.event_type.value for stored in store.paper_mutation_events()) == (
        "attempted",
        "acknowledged",
    )


def test_timeout_is_ambiguous_and_cannot_be_reposted(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_protective_plan(), OBSERVED_AT)
    broker = FakeMutationBroker(store.path)
    broker.oco_failure = httpx2.ReadTimeout("timeout")

    with store.writer() as writer:
        executor = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT,
            )
        )
        first = executor.execute_protective_oco(
            FINGERPRINT,
            store.protective_oco_plans()[0],
        )
        replay = executor.execute_protective_oco(
            FINGERPRINT,
            store.protective_oco_plans()[0],
        )

    assert first.state is PaperMutationExecutionState.AMBIGUOUS
    assert replay.state is PaperMutationExecutionState.AMBIGUOUS
    assert broker.calls == ["oco:AAA"]


def test_known_rejection_is_durable_and_not_retried(tmp_path: Path) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_protective_plan(), OBSERVED_AT)
    broker = FakeMutationBroker(store.path)
    broker.oco_failure = PaperMutationRejectedError(
        422,
        PaperMutationRequestId("request-rejected-1"),
    )

    with store.writer() as writer:
        executor = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT,
            )
        )
        result = executor.execute_protective_oco(
            FINGERPRINT,
            store.protective_oco_plans()[0],
        )

    assert result.state is PaperMutationExecutionState.REJECTED
    assert store.paper_mutation_events()[-1].event.status_code == 422


def test_known_rejection_without_request_id_is_durable_and_not_retried(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_protective_plan(), OBSERVED_AT)
    broker = FakeMutationBroker(store.path)
    broker.oco_failure = PaperMutationRejectedError(422, None)

    with store.writer() as writer:
        executor = PaperMutationExecutor(
            PaperMutationExecutorDependencies(
                writer,
                store.paper_mutation_events,
                broker,
                lambda: OBSERVED_AT,
            )
        )
        first = executor.execute_protective_oco(
            FINGERPRINT,
            store.protective_oco_plans()[0],
        )
        replay = executor.execute_protective_oco(
            FINGERPRINT,
            store.protective_oco_plans()[0],
        )

    assert first.state is PaperMutationExecutionState.REJECTED
    assert replay.state is PaperMutationExecutionState.REJECTED
    assert broker.calls == ["oco:AAA"]
