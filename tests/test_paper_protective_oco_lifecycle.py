from __future__ import annotations

import datetime as dt
from decimal import Decimal

from tests.paper_runtime_fixtures import account
from tests.trade_update_ledger_fixtures import FINGERPRINT, OBSERVED_AT
from trading_agent.broker_order_projection import BrokerOrderLedgerState
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import (
    BrokerOrderEventType,
    BrokerOrderId,
    IntentId,
    PaperBrokerState,
    PaperOrderSide,
    PaperPositionSnapshot,
)
from trading_agent.paper_mutation_intents import protective_oco_mutation_intent
from trading_agent.paper_mutation_keys import (
    paper_mutation_event_key,
    paper_mutation_key,
)
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationEventType,
)
from trading_agent.paper_mutation_store import (
    StoredPaperMutationEvent,
    StoredPaperMutationIntent,
)
from trading_agent.paper_protective_exit import (
    BlockedProtectiveExitPlan,
    NoProtectiveExitRequired,
    plan_protective_oco_exit,
)
from trading_agent.paper_protective_oco_lifecycle import (
    ProtectiveOcoResizeCancelPlan,
    plan_current_protective_oco_lifecycle,
)
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoExitPlan,
    ProtectiveOcoLegKind,
    ProtectiveOcoLegSnapshot,
    ProtectiveOcoOrderType,
    ProtectiveOcoSnapshot,
)
from trading_agent.paper_protective_oco_store import (
    StoredProtectiveOcoPlan,
    protective_oco_plan_key,
)

PARENT_ID = IntentId("orb-aaa-20260714-093600")


def test_exact_open_oco_coverage_is_a_noop() -> None:
    stored = _stored_plan(10)
    snapshot = _snapshot(stored.plan)

    decision = _decision(
        filled=10,
        position=10,
        stored=stored,
        open_snapshot=snapshot,
        history=(snapshot,),
    )

    assert decision == NoProtectiveExitRequired(PARENT_ID)


def test_additional_entry_fill_plans_only_the_old_oco_cancel() -> None:
    stored = _stored_plan(10)
    snapshot = _snapshot(stored.plan)

    decision = _decision(
        filled=15,
        position=15,
        stored=stored,
        open_snapshot=snapshot,
        history=(snapshot,),
    )

    assert isinstance(decision, ProtectiveOcoResizeCancelPlan)
    assert decision.parent_intent_id == PARENT_ID
    assert decision.source_plan_key == stored.plan_key
    assert decision.broker_order_id == snapshot.take_profit.broker_order_id
    assert decision.symbol == "AAA"


def test_pending_cancel_blocks_replacement_post() -> None:
    stored = _stored_plan(10)
    snapshot = _snapshot(stored.plan, take_profit_status="pending_cancel")

    decision = _decision(
        filled=15,
        position=15,
        stored=stored,
        open_snapshot=snapshot,
        history=(snapshot,),
    )

    assert isinstance(decision, BlockedProtectiveExitPlan)
    assert "pending_cancel" in " ".join(decision.reasons)


def test_terminal_cancel_plans_one_restart_stable_unique_replacement() -> None:
    stored = _stored_plan(10)
    canceled = _snapshot(
        stored.plan,
        take_profit_status="canceled",
        stop_status="canceled",
    )

    first = _decision(
        filled=15,
        position=15,
        stored=stored,
        open_snapshot=None,
        history=(canceled,),
    )
    replay = _decision(
        filled=15,
        position=15,
        stored=stored,
        open_snapshot=None,
        history=(canceled,),
    )

    assert isinstance(first, ProtectiveOcoExitPlan)
    assert isinstance(replay, ProtectiveOcoExitPlan)
    assert first.quantity == 15
    assert first.client_order_id != stored.plan.client_order_id
    assert replay.client_order_id == first.client_order_id


def test_saved_plan_without_attempt_can_resume_the_initial_post() -> None:
    stored = _stored_plan(10)

    decision = _decision(
        filled=10,
        position=10,
        stored=stored,
        open_snapshot=None,
        history=(),
    )

    assert isinstance(decision, ProtectiveOcoExitPlan)
    assert decision.client_order_id == stored.plan.client_order_id


def test_prior_ack_without_current_oco_history_is_fail_closed() -> None:
    stored = _stored_plan(10)

    decision = _decision(
        filled=15,
        position=15,
        stored=stored,
        open_snapshot=None,
        history=(),
        prior_event_type=PaperMutationEventType.ACKNOWLEDGED,
    )

    assert isinstance(decision, BlockedProtectiveExitPlan)
    assert "history" in " ".join(decision.reasons)


def test_recovered_absent_without_current_oco_history_can_retry() -> None:
    stored = _stored_plan(10)

    decision = _decision(
        filled=10,
        position=10,
        stored=stored,
        open_snapshot=None,
        history=(),
        prior_event_type=PaperMutationEventType.RECOVERED_ABSENT,
    )

    assert isinstance(decision, ProtectiveOcoExitPlan)
    assert decision.client_order_id == stored.plan.client_order_id


def test_one_leg_partial_fill_with_exact_remaining_coverage_is_a_noop() -> None:
    stored = _stored_plan(10)
    adjusted = _snapshot(
        stored.plan,
        take_profit_filled=2,
        stop_quantity=8,
    )

    decision = _decision(
        filled=10,
        position=8,
        stored=stored,
        open_snapshot=adjusted,
        history=(adjusted,),
    )

    assert decision == NoProtectiveExitRequired(PARENT_ID)


def test_terminal_oco_fill_with_zero_position_needs_no_replacement() -> None:
    stored = _stored_plan(10)
    filled = _snapshot(
        stored.plan,
        take_profit_status="filled",
        stop_status="canceled",
        take_profit_filled=10,
    )

    decision = _decision(
        filled=10,
        position=None,
        stored=stored,
        open_snapshot=None,
        history=(filled,),
    )

    assert decision == NoProtectiveExitRequired(PARENT_ID)


def test_both_oco_legs_filling_is_fail_closed() -> None:
    stored = _stored_plan(10)
    raced = _snapshot(
        stored.plan,
        take_profit_status="partially_filled",
        stop_status="partially_filled",
        take_profit_filled=1,
        stop_filled=1,
    )

    decision = _decision(
        filled=10,
        position=8,
        stored=stored,
        open_snapshot=raced,
        history=(raced,),
    )

    assert isinstance(decision, BlockedProtectiveExitPlan)
    assert "양쪽 leg" in " ".join(decision.reasons)


def _decision(
    *,
    filled: int,
    position: int | None,
    stored: StoredProtectiveOcoPlan,
    open_snapshot: ProtectiveOcoSnapshot | None,
    history: tuple[ProtectiveOcoSnapshot, ...],
    prior_event_type: PaperMutationEventType | None = None,
):
    positions = () if position is None else (PaperPositionSnapshot("AAA", Decimal(position), Decimal("100")),)
    open_ocos = () if open_snapshot is None else (open_snapshot,)
    broker_state = PaperBrokerState(account(), (), positions, open_ocos)
    return plan_current_protective_oco_lifecycle(
        _ledger(filled, (stored,), prior_event_type=prior_event_type),
        broker_state,
        history,
        PARENT_ID,
    )


def _ledger(
    filled: int,
    plans: tuple[StoredProtectiveOcoPlan, ...],
    *,
    prior_event_type: PaperMutationEventType | None = None,
) -> ReconciliationLedger:
    mutation_intents: tuple[StoredPaperMutationIntent, ...] = ()
    mutation_events: tuple[StoredPaperMutationEvent, ...] = ()
    if prior_event_type is not None:
        mutation_intent = protective_oco_mutation_intent(FINGERPRINT, plans[-1])
        mutation_key = paper_mutation_key(mutation_intent)
        event = PaperMutationEvent(
            1,
            OBSERVED_AT,
            prior_event_type,
            "request-1",
            200,
            BrokerOrderId("oco-parent-1"),
            "a" * 64,
        )
        mutation_intents = (StoredPaperMutationIntent(mutation_key, mutation_intent),)
        mutation_events = (
            StoredPaperMutationEvent(
                1,
                paper_mutation_event_key(mutation_key, event),
                mutation_key,
                event,
            ),
        )
    return ReconciliationLedger(
        (_intent(),),
        frozenset({PARENT_ID}),
        FINGERPRINT,
        order_states=(_order_state(filled),),
        protective_oco_plans=plans,
        paper_mutation_intents=mutation_intents,
        paper_mutation_events=mutation_events,
    )


def _intent() -> StoredIntent:
    return StoredIntent(
        PARENT_ID,
        "orb",
        "paper-smoke-v1",
        "AAA",
        "2026-07-14T09:36:00-04:00",
        PaperOrderSide.BUY,
        Decimal("10"),
        Decimal("9.75"),
        Decimal("10.25"),
        Decimal("10.5"),
        100,
    )


def _order_state(filled: int) -> BrokerOrderLedgerState:
    return BrokerOrderLedgerState(
        intent_id=PARENT_ID,
        broker_order_ids=(BrokerOrderId("entry-1"),),
        terminal_event_types=(BrokerOrderEventType.PARTIAL_FILL,),
        cumulative_filled_quantity=Decimal(filled),
        complete_fill=False,
        terminal=False,
        has_fill_evidence=True,
        anomaly_reasons=(),
        execution_detail_complete=True,
        execution_average_price=Decimal("10.05"),
    )


def _stored_plan(quantity: int) -> StoredProtectiveOcoPlan:
    planned = plan_protective_oco_exit(
        _intent(),
        _order_state(quantity),
        PaperPositionSnapshot("AAA", Decimal(quantity), Decimal("100")),
    )
    assert isinstance(planned, ProtectiveOcoExitPlan)
    return StoredProtectiveOcoPlan(
        protective_oco_plan_key(planned),
        OBSERVED_AT.isoformat(),
        planned,
    )


def _snapshot(
    plan: ProtectiveOcoExitPlan,
    *,
    take_profit_status: str = "new",
    stop_status: str = "new",
    take_profit_filled: int = 0,
    stop_filled: int = 0,
    stop_quantity: int | None = None,
) -> ProtectiveOcoSnapshot:
    take_profit = ProtectiveOcoLegSnapshot(
        ProtectiveOcoLegKind.TAKE_PROFIT,
        BrokerOrderId("oco-parent-1"),
        plan.client_order_id,
        plan.symbol,
        plan.side,
        take_profit_status,
        Decimal(plan.quantity),
        Decimal(take_profit_filled),
        ProtectiveOcoOrderType.LIMIT,
        plan.take_profit_limit,
        None,
        "day",
        False,
    )
    stop = ProtectiveOcoLegSnapshot(
        ProtectiveOcoLegKind.STOP_LOSS,
        BrokerOrderId("oco-stop-1"),
        "stop-client-1",
        plan.symbol,
        plan.side,
        stop_status,
        Decimal(plan.quantity if stop_quantity is None else stop_quantity),
        Decimal(stop_filled),
        ProtectiveOcoOrderType.STOP,
        None,
        plan.stop_price,
        "day",
        False,
    )
    return ProtectiveOcoSnapshot(OBSERVED_AT + dt.timedelta(seconds=1), take_profit, stop)
