from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from tests.paper_entry_mutation_fixtures import FakeEntryMutationBroker
from tests.paper_runtime_fixtures import candidate, latest_bar
from tests.trade_update_ledger_fixtures import FINGERPRINT
from trading_agent.alpaca_paper_config import AlpacaPaperCredentials
from trading_agent.alpaca_paper_order_stream import (
    PaperOrderStreamHeartbeat,
    PaperStreamEpoch,
    PaperTradeUpdateFrame,
)
from trading_agent.alpaca_trade_updates import parse_alpaca_trade_update
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.execution_store import ExecutionStore
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperMarketClockSnapshot,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_mutation_arm import PAPER_MUTATION_ARM_VALUE, PaperMutationArm
from trading_agent.paper_mutation_executor_models import PaperMutationExecutionState
from trading_agent.paper_operating_mutation_models import (
    PaperEntryMutationExecution,
    PaperProtectiveMutationExecution,
    PaperSafetyMutationExecution,
)
from trading_agent.paper_operating_session_models import (
    PaperOperatingSession,
    PaperOrderAdmissionRequest,
)
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoExitPlan,
    ProtectiveOcoLegKind,
    ProtectiveOcoLegSnapshot,
    ProtectiveOcoOrderType,
    ProtectiveOcoSnapshot,
)
from trading_agent.paper_reconciliation import (
    PaperReconciliationSnapshot,
    reconcile_paper_state,
)
from trading_agent.paper_safety_models import PaperSafetyPhase
from trading_agent.paper_stream_owner import PaperStreamOwnerDependencies
from trading_agent.paper_stream_recovery import PaperRecoveryState
from trading_agent.paper_trade_update_runtime import (
    PaperOperatingSessionDependencies,
    _open_paper_operating_session,
)

_EASTERN = dt.timezone(dt.timedelta(hours=-4))
_ARM = PaperMutationArm(PAPER_MUTATION_ARM_VALUE)


class _PhaseStream:
    def __init__(self, started_at: dt.datetime) -> None:
        self._started_at = started_at
        self.heartbeat_count = 0

    @property
    def connection_epoch(self) -> PaperStreamEpoch:
        return PaperStreamEpoch("fake-smoke-epoch")

    def heartbeat(self, timeout_seconds: float) -> PaperOrderStreamHeartbeat:
        assert timeout_seconds == 5.0
        self.heartbeat_count += 1
        pong_at = self._started_at + dt.timedelta(seconds=self.heartbeat_count - 2)
        return PaperOrderStreamHeartbeat(
            self.connection_epoch,
            self._started_at - dt.timedelta(seconds=2),
            self._started_at - dt.timedelta(seconds=2),
            pong_at,
        )

    def receive_trade_update_frame(self, timeout_seconds: float) -> PaperTradeUpdateFrame:
        raise AssertionError(f"unexpected trade update receive: {timeout_seconds}")


def test_fake_broker_smoke_runs_entry_to_protection_to_staged_eod_flatten(
    tmp_path: Path,
) -> None:
    store = ExecutionStore(tmp_path / "execution.sqlite3")
    broker = FakeEntryMutationBroker(store.path)
    credentials = AlpacaPaperCredentials("test-key", "test-secret")

    entry_at = dt.datetime(2026, 7, 14, 13, 36, 2, tzinfo=dt.UTC)
    protection_at = dt.datetime(2026, 7, 14, 13, 40, 2, tzinfo=dt.UTC)
    first_eod_at = dt.datetime(2026, 7, 14, 19, 55, 2, tzinfo=dt.UTC)
    second_eod_at = dt.datetime(2026, 7, 14, 19, 56, 2, tzinfo=dt.UTC)

    @contextmanager
    def open_at(now: dt.datetime) -> Iterator[PaperOperatingSession]:
        stream = _PhaseStream(now)

        @contextmanager
        def stream_opener(_: AlpacaPaperCredentials) -> Iterator[_PhaseStream]:
            yield stream

        @contextmanager
        def broker_opener(
            _: AlpacaPaperCredentials,
        ) -> Iterator[FakeEntryMutationBroker]:
            yield broker

        def current_time() -> dt.datetime:
            return now + dt.timedelta(seconds=max(0, stream.heartbeat_count - 2))

        def observed_at() -> dt.datetime:
            return now + dt.timedelta(seconds=stream.heartbeat_count - 1.5)

        def current_state(
            _: AlpacaPaperCredentials,
            ledger: ReconciliationLedger,
        ) -> PaperRecoveryState:
            return _recovery_state(store, broker, ledger, observed_at())

        def runtime_state(
            _: AlpacaPaperCredentials,
        ) -> tuple[PaperBrokerState, PaperMarketClockSnapshot]:
            current = observed_at()
            state = _recovery_state(
                store,
                broker,
                store.reconciliation_ledger(),
                current,
            )
            return state.broker_state, _market_clock(current)

        dependencies = PaperOperatingSessionDependencies(
            PaperStreamOwnerDependencies(current_state, stream_opener, current_time),
            runtime_state,
            current_time,
            broker_opener,
        )
        with _open_paper_operating_session(credentials, store, dependencies) as session:
            yield session

    request = PaperOrderAdmissionRequest(latest_bar(), candidate(), 1, 20.0)
    with open_at(entry_at) as session:
        entry = session.execute_entry(request, _ARM)

    assert isinstance(entry, PaperEntryMutationExecution)
    assert entry.result.state is PaperMutationExecutionState.ACKNOWLEDGED
    stored_intent = store.intents()[0]

    with store.writer() as writer:
        _ = writer.append_trade_update(
            _filled_entry_update(stored_intent.intent_id, stored_intent.quantity, entry_at),
            account_fingerprint=FINGERPRINT,
            connection_epoch="fake-smoke-epoch",
            received_at=entry_at + dt.timedelta(seconds=10),
        )

    with open_at(protection_at) as session:
        protection = session.execute_protective_oco(
            stored_intent.intent_id,
            _ARM,
        )

    assert isinstance(protection, PaperProtectiveMutationExecution)
    assert protection.result.state is PaperMutationExecutionState.ACKNOWLEDGED
    assert protection.plan.quantity == stored_intent.quantity

    with open_at(first_eod_at) as session:
        first_eod = session.execute_safety_actions(_ARM)

    assert isinstance(first_eod, PaperSafetyMutationExecution)
    assert first_eod.plan.phase is PaperSafetyPhase.EOD_FLATTEN
    assert tuple(result.state for result in first_eod.results) == (PaperMutationExecutionState.ACKNOWLEDGED,)

    with open_at(second_eod_at) as session:
        second_eod = session.execute_safety_actions(_ARM)

    assert isinstance(second_eod, PaperSafetyMutationExecution)
    assert second_eod.plan.phase is PaperSafetyPhase.EOD_FLATTEN
    assert tuple(result.state for result in second_eod.results) == (PaperMutationExecutionState.ACKNOWLEDGED,)
    assert broker.calls == [
        "entry:AAPL",
        "oco:AAPL",
        "cancel:oco-parent-1",
        "close:AAPL",
    ]

    ledger = store.reconciliation_ledger()
    final = reconcile_paper_state(
        PaperReconciliationSnapshot(
            _account(second_eod_at),
            (),
            (),
            ledger.intents,
            ledger.unresolved_intent_ids,
            ledger.account_fingerprint,
            ledger.order_states,
        )
    )
    assert final.ready is True
    assert final.reasons == ()


def _recovery_state(
    store: ExecutionStore,
    broker: FakeEntryMutationBroker,
    ledger: ReconciliationLedger,
    observed_at: dt.datetime,
) -> PaperRecoveryState:
    if not ledger.intents:
        return PaperRecoveryState(PaperBrokerState(_account(observed_at), (), ()), ())

    intent = ledger.intents[0]
    order_state = ledger.order_states[0]
    filled = order_state.cumulative_filled_quantity
    entry = PaperOrderSnapshot(
        BrokerOrderId("entry-1"),
        intent.intent_id,
        intent.symbol,
        intent.side,
        "filled" if filled else "accepted",
        Decimal(intent.quantity),
        filled,
        intent.entry_limit,
        "day",
        False,
        Decimal("100") if filled else None,
    )
    if not filled:
        return PaperRecoveryState(
            PaperBrokerState(_account(observed_at), (), ()),
            (entry,),
        )

    closed = f"close:{intent.symbol}" in broker.calls
    canceled = "cancel:oco-parent-1" in broker.calls
    has_protection = "oco:AAPL" in broker.calls and store.protective_oco_plans()
    position = PaperPositionSnapshot(
        intent.symbol,
        filled,
        filled * Decimal("100"),
    )
    if closed:
        return PaperRecoveryState(
            PaperBrokerState(_account(observed_at), (), ()),
            (),
            recent_orders=(entry,),
        )
    if not has_protection:
        return PaperRecoveryState(
            PaperBrokerState(_account(observed_at), (), (position,)),
            (),
            recent_orders=(entry,),
        )

    plan = store.protective_oco_plans()[-1].plan
    protection = _protection(plan, observed_at, "canceled" if canceled else "new")
    return PaperRecoveryState(
        PaperBrokerState(
            _account(observed_at),
            (),
            (position,),
            () if canceled else (protection,),
        ),
        (),
        recent_orders=(entry,),
        protective_ocos=(protection,),
    )


def _account(observed_at: dt.datetime) -> PaperAccountSnapshot:
    return PaperAccountSnapshot(
        observed_at,
        "ACTIVE",
        False,
        Decimal("30000"),
        Decimal("30000"),
        Decimal("60000"),
        FINGERPRINT,
    )


def _market_clock(observed_at: dt.datetime) -> PaperMarketClockSnapshot:
    market_time = observed_at.astimezone(_EASTERN)
    return PaperMarketClockSnapshot(
        observed_at,
        market_time,
        True,
        dt.datetime(2026, 7, 15, 9, 30, tzinfo=_EASTERN),
        dt.datetime(2026, 7, 14, 16, 0, tzinfo=_EASTERN),
    )


def _protection(
    plan: ProtectiveOcoExitPlan,
    observed_at: dt.datetime,
    status: str,
) -> ProtectiveOcoSnapshot:
    quantity = Decimal(plan.quantity)
    return ProtectiveOcoSnapshot(
        observed_at,
        ProtectiveOcoLegSnapshot(
            ProtectiveOcoLegKind.TAKE_PROFIT,
            BrokerOrderId("oco-parent-1"),
            plan.client_order_id,
            plan.symbol,
            plan.side,
            status,
            quantity,
            Decimal(0),
            ProtectiveOcoOrderType.LIMIT,
            plan.take_profit_limit,
            None,
            "day",
            False,
        ),
        ProtectiveOcoLegSnapshot(
            ProtectiveOcoLegKind.STOP_LOSS,
            BrokerOrderId("oco-stop-1"),
            "oco-stop-client-1",
            plan.symbol,
            plan.side,
            status,
            quantity,
            Decimal(0),
            ProtectiveOcoOrderType.STOP,
            None,
            plan.stop_price,
            "day",
            False,
        ),
    )


def _filled_entry_update(intent_id: str, quantity: int, observed_at: dt.datetime):
    timestamp = observed_at.isoformat().replace("+00:00", "Z")
    return parse_alpaca_trade_update(
        json.dumps(
            {
                "stream": "trade_updates",
                "data": {
                    "event": "fill",
                    "event_id": "fake-entry-fill",
                    "execution_id": "fake-entry-execution",
                    "timestamp": timestamp,
                    "price": "100",
                    "qty": str(quantity),
                    "position_qty": str(quantity),
                    "order": {
                        "id": "entry-1",
                        "client_order_id": intent_id,
                        "asset_class": "us_equity",
                        "symbol": "AAPL",
                        "side": "buy",
                        "status": "filled",
                        "qty": str(quantity),
                        "filled_qty": str(quantity),
                        "filled_avg_price": "100",
                        "limit_price": "100",
                        "time_in_force": "day",
                        "extended_hours": False,
                        "updated_at": timestamp,
                    },
                },
            }
        )
    )
