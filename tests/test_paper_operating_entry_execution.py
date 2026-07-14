from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from tests.paper_entry_mutation_fixtures import FakeEntryMutationBroker
from tests.paper_runtime_fixtures import candidate, latest_bar, market_clock
from tests.paper_trade_update_ingestion_fixtures import TradeUpdateStream, recovery_state
from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import PaperBrokerState, PaperMarketClockSnapshot
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE, PaperMutationArm
from trading_agent.paper_mutation_executor_models import PaperMutationExecutionState
from trading_agent.paper_operating_mutation_models import PaperEntryMutationExecution
from trading_agent.paper_operating_session_models import (
    PaperOperatingSession,
    PaperOrderAdmissionRequest,
)
from trading_agent.paper_order_gate_models import BlockedPaperOrderGateDecision
from trading_agent.paper_stream_owner import PaperStreamOwnerDependencies
from trading_agent.paper_stream_recovery import PaperRecoveryState
from trading_agent.paper_trade_update_runtime import (
    PaperOperatingSessionDependencies,
    _open_paper_operating_session,
)


def test_operating_session_surface_exposes_entry_execution() -> None:
    assert "execute_entry" in PaperOperatingSession.__dict__


def test_current_epoch_entry_executes_once_with_explicit_arm_and_reconciles(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    with store.writer() as writer:
        _ = writer.bind_account(FINGERPRINT, OBSERVED_AT)
    broker = FakeEntryMutationBroker(store.path)
    stream = TradeUpdateStream()
    evaluated_at = OBSERVED_AT + dt.timedelta(seconds=4)

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield stream

    @contextmanager
    def broker_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[FakeEntryMutationBroker]:
        yield broker

    def current_state(
        _: AlpacaPaperCredentials,
        ledger: ReconciliationLedger,
    ) -> PaperRecoveryState:
        observed_at = OBSERVED_AT + dt.timedelta(seconds=stream.heartbeat_count - 1.5)
        state = recovery_state(ledger.unresolved_intent_ids, observed_at)
        if not state.targeted_orders:
            return state
        stored = store.intents()[0]
        targeted = tuple(
            replace(
                order,
                client_order_id=stored.intent_id,
                symbol=stored.symbol,
                side=stored.side,
                quantity=Decimal(stored.quantity),
                limit_price=stored.entry_limit,
            )
            for order in state.targeted_orders
        )
        return replace(state, targeted_orders=targeted)

    def runtime_state(
        _: AlpacaPaperCredentials,
    ) -> tuple[PaperBrokerState, PaperMarketClockSnapshot]:
        observed_at = evaluated_at - dt.timedelta(milliseconds=500)
        state = recovery_state(
            store.reconciliation_ledger().unresolved_intent_ids,
            observed_at,
        )
        clock = replace(
            market_clock(),
            observed_at=observed_at,
            market_timestamp=evaluated_at.astimezone(dt.timezone(dt.timedelta(hours=-4))),
        )
        return state.broker_state, clock

    dependencies = PaperOperatingSessionDependencies(
        PaperStreamOwnerDependencies(current_state, stream_opener, lambda: evaluated_at),
        runtime_state,
        lambda: evaluated_at,
        broker_opener,
    )
    request = PaperOrderAdmissionRequest(latest_bar(), candidate(), 100, 20.0)
    arm = PaperMutationArm(PAPER_MUTATION_ARM_VALUE)

    with _open_paper_operating_session(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        dependencies,
    ) as session:
        first = session.execute_entry(request, arm)
        replay = session.execute_entry(request, arm)

    assert isinstance(first, PaperEntryMutationExecution)
    assert first.result.state is PaperMutationExecutionState.ACKNOWLEDGED
    assert first.reconciled_at > evaluated_at
    assert isinstance(replay, BlockedPaperOrderGateDecision)
    assert broker.calls == ["entry:AAPL"]
