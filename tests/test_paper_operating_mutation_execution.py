from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest

from tests.paper_runtime_fixtures import market_clock
from tests.paper_trade_update_ingestion_fixtures import (
    TradeUpdateStream,
    broker_state,
    recovery_state,
)
from tests.test_paper_mutation_executor import FakeMutationBroker, _oco_snapshot
from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
    trade_update,
)
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    PaperOrderStreamHeartbeat,
    PaperStreamEpoch,
)
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.paper_execution_models import (
    PaperBrokerState,
    PaperMarketClockSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_mutation_executor_models import PaperMutationExecutionState
from trading_agent.paper_mutation_recovery_models import PaperMutationRecoveryState
from trading_agent.paper_operating_mutation_models import (
    PaperProtectiveMutationExecution,
    PaperSafetyMutationExecution,
)
from trading_agent.paper_operating_session_models import PaperOperatingSession
from trading_agent.paper_protective_exit import BlockedProtectiveExitPlan
from trading_agent.paper_safety_models import PaperSafetyPhase
from trading_agent.paper_stream_owner import PaperStreamOwnerDependencies
from trading_agent.paper_stream_recovery import PaperRecoveryState
from trading_agent.paper_stream_recovery_models import PaperProtectiveOcoMutationLookup
from trading_agent.paper_trade_update_runtime import (
    PaperOperatingSessionDependencies,
    _open_paper_operating_session,
)


def test_operating_session_surface_exposes_current_epoch_safety_execution() -> None:
    assert "execute_safety_actions" in PaperOperatingSession.__dict__
    assert "execute_protective_oco" in PaperOperatingSession.__dict__


def test_current_epoch_entry_cutoff_executes_once_and_reconciles(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    broker = FakeMutationBroker(store.path)
    evaluated_at = dt.datetime(2026, 7, 14, 19, 30, 2, tzinfo=dt.UTC)

    class CurrentStream(TradeUpdateStream):
        def heartbeat(self, timeout_seconds: float) -> PaperOrderStreamHeartbeat:
            assert timeout_seconds == 5.0
            self.heartbeat_count += 1
            pong_at = evaluated_at + dt.timedelta(seconds=self.heartbeat_count - 2)
            return PaperOrderStreamHeartbeat(
                self.connection_epoch,
                evaluated_at - dt.timedelta(seconds=2),
                evaluated_at - dt.timedelta(seconds=2),
                pong_at,
            )

    stream = CurrentStream()

    def current_time() -> dt.datetime:
        return evaluated_at + dt.timedelta(seconds=max(0, stream.heartbeat_count - 2))

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield stream

    @contextmanager
    def broker_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[FakeMutationBroker]:
        yield broker

    def current_state(
        _: AlpacaPaperCredentials,
        ledger: ReconciliationLedger,
    ) -> PaperRecoveryState:
        observed_at = evaluated_at + dt.timedelta(
            seconds=stream.heartbeat_count - 1,
            milliseconds=-500,
        )
        state = recovery_state(ledger.unresolved_intent_ids, observed_at)
        if broker.calls:
            canceled = tuple(replace(order, status="canceled") for order in state.targeted_orders)
            return replace(
                state,
                broker_state=replace(state.broker_state, open_orders=()),
                targeted_orders=canceled,
            )
        return state

    def runtime_state(
        _: AlpacaPaperCredentials,
    ) -> tuple[PaperBrokerState, PaperMarketClockSnapshot]:
        observed_at = evaluated_at + dt.timedelta(
            seconds=stream.heartbeat_count - 1,
            milliseconds=-500,
        )
        state = recovery_state(store.reconciliation_ledger().unresolved_intent_ids, observed_at)
        clock = replace(
            market_clock(),
            observed_at=observed_at,
            market_timestamp=observed_at.astimezone(dt.timezone(dt.timedelta(hours=-4))),
        )
        return replace(state.broker_state, open_orders=state.targeted_orders), clock

    dependencies = PaperOperatingSessionDependencies(
        PaperStreamOwnerDependencies(current_state, stream_opener, current_time),
        runtime_state,
        current_time,
        broker_opener,
    )

    with _open_paper_operating_session(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        dependencies,
    ) as session:
        first = session.execute_safety_actions()
        replay = session.execute_safety_actions()

    assert isinstance(first, PaperSafetyMutationExecution)
    assert first.plan.phase is PaperSafetyPhase.ENTRY_CUTOFF
    assert tuple(result.state for result in first.results) == (PaperMutationExecutionState.ACKNOWLEDGED,)
    assert broker.calls == ["cancel:paper-order-1"]
    assert isinstance(replay, PaperSafetyMutationExecution)
    assert replay.results == ()
    assert first.reconciled_at > evaluated_at
    assert tuple(event.event.event_type.value for event in store.paper_mutation_events()) == (
        "attempted",
        "acknowledged",
    )


@pytest.mark.parametrize("mode", ("acknowledged", "timeout", "epoch_change"))
def test_current_epoch_partial_fill_submits_one_protective_oco_and_recovers_timeout(
    tmp_path: Path,
    mode: str,
) -> None:
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.append_trade_update(
            trade_update(),
            account_fingerprint=FINGERPRINT,
            connection_epoch="epoch-from-stream",
            received_at=OBSERVED_AT,
        )
    broker = FakeMutationBroker(store.path)
    times_out = mode == "timeout"
    if times_out:
        broker.oco_failure = httpx2.ReadTimeout("timeout")

    class PlanEpochStream(TradeUpdateStream):
        @property
        def connection_epoch(self) -> PaperStreamEpoch:
            if mode == "epoch_change" and store.protective_oco_plans():
                return PaperStreamEpoch("epoch-after-plan")
            return PaperStreamEpoch("epoch-from-stream")

    stream = PlanEpochStream()

    @contextmanager
    def stream_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[TradeUpdateStream]:
        yield stream

    @contextmanager
    def broker_opener(
        _: AlpacaPaperCredentials,
    ) -> Iterator[FakeMutationBroker]:
        yield broker

    def current_state(
        _: AlpacaPaperCredentials,
        ledger: ReconciliationLedger,
    ) -> PaperRecoveryState:
        observed_at = OBSERVED_AT + dt.timedelta(seconds=stream.heartbeat_count - 1.5)
        state = recovery_state(ledger.unresolved_intent_ids, observed_at)
        targeted = tuple(
            replace(
                order,
                filled_quantity=Decimal("10"),
                filled_average_price=Decimal("10.05"),
            )
            for order in state.targeted_orders
        )
        position = PaperPositionSnapshot("AAA", Decimal("10"), Decimal("100.5"))
        positioned = replace(state.broker_state, positions=(position,))
        if broker.calls:
            plan = store.protective_oco_plans()[-1].plan
            raw_protection = _oco_snapshot()
            protection = replace(
                raw_protection,
                observed_at=observed_at,
                take_profit=replace(
                    raw_protection.take_profit,
                    client_order_id=plan.client_order_id,
                    quantity=Decimal(plan.quantity),
                    limit_price=plan.take_profit_limit,
                ),
                stop_loss=replace(
                    raw_protection.stop_loss,
                    quantity=Decimal(plan.quantity),
                    stop_price=plan.stop_price,
                ),
            )
            mutation_is_unresolved = times_out and store.paper_mutation_events()[-1].event.event_type.value in (
                "attempted",
                "ambiguous",
            )
            lookups = (
                (
                    PaperProtectiveOcoMutationLookup(
                        store.paper_mutation_intents()[-1].mutation_key,
                        observed_at,
                        protection,
                    ),
                )
                if mutation_is_unresolved
                else ()
            )
            return replace(
                state,
                broker_state=replace(positioned, protective_ocos=(protection,)),
                targeted_orders=targeted,
                protective_ocos=(protection,),
                mutation_lookups=lookups,
            )
        return replace(
            state,
            broker_state=positioned,
            targeted_orders=targeted,
        )

    dependencies = PaperOperatingSessionDependencies(
        PaperStreamOwnerDependencies(
            current_state,
            stream_opener,
            lambda: OBSERVED_AT + dt.timedelta(seconds=4),
        ),
        lambda _: (broker_state(OBSERVED_AT), market_clock()),
        lambda: OBSERVED_AT + dt.timedelta(seconds=4),
        broker_opener,
    )

    with _open_paper_operating_session(
        AlpacaPaperCredentials("test-key", "test-secret"),
        store,
        dependencies,
    ) as session:
        first = session.execute_protective_oco(store.intents()[0].intent_id)
        if mode == "epoch_change":
            assert isinstance(first, BlockedProtectiveExitPlan)
            assert "연결 세대" in first.reasons[0]
            assert broker.calls == []
            return
        replay = session.execute_protective_oco(store.intents()[0].intent_id)

    assert isinstance(first, PaperProtectiveMutationExecution)
    assert first.plan.quantity == 10
    expected = PaperMutationExecutionState.AMBIGUOUS if times_out else PaperMutationExecutionState.ACKNOWLEDGED
    assert first.result.state is expected
    assert tuple(recovery.state for recovery in first.recoveries) == (
        (PaperMutationRecoveryState.ACKNOWLEDGED,) if times_out else ()
    )
    assert isinstance(replay, PaperProtectiveMutationExecution)
    assert replay.result.state is PaperMutationExecutionState.ALREADY_ACKNOWLEDGED
    assert broker.calls == ["oco:AAA"]
