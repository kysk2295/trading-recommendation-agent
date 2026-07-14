from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from tests.trade_update_ledger_fixtures import (
    FINGERPRINT,
    OBSERVED_AT,
    initialized_store,
    intent,
)
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    PaperAccountSnapshot,
    PaperBrokerState,
    PaperOrderSide,
    PaperPositionSnapshot,
)
from trading_agent.paper_mutation_intents import (
    protective_oco_mutation_intent,
    safety_action_mutation_intent,
)
from trading_agent.paper_mutation_keys import paper_mutation_key
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationEventType,
)
from trading_agent.paper_mutation_recovery import (
    PaperMutationRecovery,
    PaperMutationRecoveryDependencies,
)
from trading_agent.paper_mutation_recovery_models import (
    PaperMutationRecoverySnapshot,
    PaperMutationRecoveryState,
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
    PaperClosePositionAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
)
from trading_agent.paper_stream_recovery import PaperRecoveryState
from trading_agent.paper_stream_recovery_models import (
    PaperProtectiveOcoMutationLookup,
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


def _plan() -> ProtectiveOcoExitPlan:
    return ProtectiveOcoExitPlan(
        ProtectiveOcoClientOrderId("protect-" + "a" * 40),
        intent().intent_id,
        "AAA",
        PaperOrderSide.SELL,
        10,
        Decimal("10.5"),
        Decimal("9.75"),
    )


def _oco(observed_at: dt.datetime) -> ProtectiveOcoSnapshot:
    take_profit = ProtectiveOcoLegSnapshot(
        ProtectiveOcoLegKind.TAKE_PROFIT,
        BrokerOrderId("oco-parent-1"),
        _plan().client_order_id,
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
    stop = replace(
        take_profit,
        kind=ProtectiveOcoLegKind.STOP_LOSS,
        broker_order_id=BrokerOrderId("oco-stop-1"),
        client_order_id="oco-stop-client-1",
        order_type=ProtectiveOcoOrderType.STOP,
        limit_price=None,
        stop_price=Decimal("9.75"),
    )
    return ProtectiveOcoSnapshot(observed_at, take_profit, stop)


def _recovery_snapshot(
    state: PaperRecoveryState,
) -> PaperMutationRecoverySnapshot:
    return PaperMutationRecoverySnapshot(
        "epoch-1",
        OBSERVED_AT + dt.timedelta(seconds=2),
        OBSERVED_AT + dt.timedelta(seconds=12),
        state,
    )


def _record_ambiguous(writer, mutation_intent) -> None:
    from trading_agent.paper_mutation_keys import paper_mutation_key

    _ = writer.save_paper_mutation_intent(mutation_intent)
    key = paper_mutation_key(mutation_intent)
    _ = writer.append_paper_mutation_event(
        key,
        PaperMutationEvent(
            1,
            OBSERVED_AT,
            PaperMutationEventType.ATTEMPTED,
            None,
            None,
            None,
            "1" * 64,
        ),
    )
    _ = writer.append_paper_mutation_event(
        key,
        PaperMutationEvent(
            1,
            OBSERVED_AT + dt.timedelta(seconds=1),
            PaperMutationEventType.AMBIGUOUS,
            None,
            None,
            None,
            "2" * 64,
        ),
    )


def _recover(store, writer, snapshot: PaperMutationRecoverySnapshot):
    return PaperMutationRecovery(
        PaperMutationRecoveryDependencies(
            writer,
            store.paper_mutation_intents,
            store.paper_mutation_events,
            store.protective_oco_plans,
        )
    ).recover(snapshot)


def test_partial_position_change_without_close_order_stays_ambiguous(
    tmp_path: Path,
) -> None:
    store = initialized_store(tmp_path)
    action = PaperClosePositionAction("AAA", PaperOrderSide.SELL, Decimal(10))
    plan = PaperSafetyPlan(
        FINGERPRINT,
        OBSERVED_AT,
        dt.date(2026, 7, 14),
        PaperSafetyPhase.KILL_SWITCH,
        Decimal("-301"),
        Decimal("-301"),
        (action,),
    )
    with store.writer() as writer:
        _ = writer.save_paper_safety_plan(plan)
    stored = store.paper_safety_plans()[0]
    with store.writer() as writer:
        _record_ambiguous(writer, safety_action_mutation_intent(stored, 0, action))
        state = PaperRecoveryState(
            PaperBrokerState(
                _account(OBSERVED_AT + dt.timedelta(seconds=10)),
                (),
                (PaperPositionSnapshot("AAA", Decimal(5), Decimal(50)),),
            ),
            (),
        )
        results = _recover(store, writer, _recovery_snapshot(state))

    assert results[0].state is PaperMutationRecoveryState.UNRESOLVED
    assert store.paper_mutation_events()[-1].event.event_type is PaperMutationEventType.AMBIGUOUS


def test_targeted_404_conflicting_with_generic_oco_stays_unresolved(
    tmp_path: Path,
) -> None:
    # Given: targeted 404 evidence conflicts with a matching generic OCO snapshot.
    store = initialized_store(tmp_path)
    with store.writer() as writer:
        _ = writer.save_protective_oco_plan(_plan(), OBSERVED_AT)
    stored = store.protective_oco_plans()[0]
    mutation = protective_oco_mutation_intent(FINGERPRINT, stored)
    observed_at = OBSERVED_AT + dt.timedelta(seconds=10)
    protection = _oco(observed_at)
    with store.writer() as writer:
        _record_ambiguous(writer, mutation)
        state = PaperRecoveryState(
            PaperBrokerState(_account(observed_at), (), (), (protection,)),
            (),
            protective_ocos=(protection,),
            mutation_lookups=(
                PaperProtectiveOcoMutationLookup(
                    paper_mutation_key(mutation),
                    observed_at,
                    None,
                ),
            ),
        )

        # When: recovery evaluates the contradictory REST evidence.
        results = _recover(store, writer, _recovery_snapshot(state))

    # Then: it cannot authorize a duplicate protective POST.
    assert results[0].state is PaperMutationRecoveryState.UNRESOLVED
