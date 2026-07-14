from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, assert_never

from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_entry_mutation_recovery import decide_entry_mutation_recovery
from trading_agent.paper_execution_models import BrokerOrderId, PaperOrderSide
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEvent,
    PaperMutationOperation,
)
from trading_agent.paper_mutation_recovery_models import (
    PaperMutationRecoveryResult,
    PaperMutationRecoverySnapshot,
    PaperMutationRecoveryState,
)
from trading_agent.paper_mutation_store import StoredPaperMutationIntent
from trading_agent.paper_protective_oco_store import (
    StoredProtectiveOcoPlan,
    protective_oco_snapshot_matches_plan,
)
from trading_agent.paper_stream_recovery_models import (
    PaperCancelOrderMutationLookup,
    PaperEntryOrderMutationLookup,
    PaperProtectiveOcoMutationLookup,
)

RECOVERY_SETTLE_DELAY: Final = dt.timedelta(seconds=30)
RECOVERY_MAX_EVIDENCE_AGE: Final = dt.timedelta(days=1)
_CANCEL_EFFECT_STATUSES: Final = frozenset(
    {
        "pending_cancel",
        "canceled",
        "filled",
        "expired",
        "rejected",
        "done_for_day",
        "stopped",
        "calculated",
        "replaced",
    }
)
_CANCEL_RETRYABLE_STATUSES: Final = frozenset({"new", "accepted", "pending_new", "partially_filled"})
_FAILED_CLOSE_STATUSES: Final = frozenset({"canceled", "expired", "rejected", "failed"})


@dataclass(frozen=True, slots=True)
class PaperMutationRecoveryCase:
    stored_intent: StoredPaperMutationIntent
    attempted: PaperMutationEvent
    snapshot: PaperMutationRecoverySnapshot
    order_intents: tuple[StoredIntent, ...]
    protective_plans: tuple[StoredProtectiveOcoPlan, ...]


@dataclass(frozen=True, slots=True)
class _ObservedOrder:
    broker_order_id: BrokerOrderId
    symbol: str
    status: str


def decide_paper_mutation_recovery(
    case: PaperMutationRecoveryCase,
) -> PaperMutationRecoveryResult:
    operation = case.stored_intent.intent.operation
    match operation:
        case PaperMutationOperation.SUBMIT_ENTRY:
            return decide_entry_mutation_recovery(
                case.stored_intent,
                case.attempted,
                case.snapshot,
                case.order_intents,
            )
        case PaperMutationOperation.SUBMIT_PROTECTIVE_OCO:
            return _oco_decision(case)
        case PaperMutationOperation.CANCEL_ORDER:
            return _cancel_decision(case)
        case PaperMutationOperation.CLOSE_POSITION:
            return _close_decision(case)
        case unreachable:
            assert_never(unreachable)


def _oco_decision(case: PaperMutationRecoveryCase) -> PaperMutationRecoveryResult:
    source_key = case.stored_intent.intent.protective_plan_key
    plans = tuple(plan for plan in case.protective_plans if plan.plan_key == source_key)
    if len(plans) != 1:
        return _unresolved(case)
    generic_matches = tuple(
        snapshot
        for snapshot in case.snapshot.state.protective_ocos
        if protective_oco_snapshot_matches_plan(snapshot, plans[0].plan)
    )
    lookups = tuple(
        lookup
        for lookup in case.snapshot.state.mutation_lookups
        if lookup.mutation_key == case.stored_intent.mutation_key
    )
    if len(lookups) != 1:
        return _unresolved(case)
    match lookups[0]:
        case PaperProtectiveOcoMutationLookup(snapshot=snapshot):
            if snapshot is not None and protective_oco_snapshot_matches_plan(
                snapshot,
                plans[0].plan,
            ):
                return PaperMutationRecoveryResult(
                    case.stored_intent.mutation_key,
                    PaperMutationRecoveryState.ACKNOWLEDGED,
                    snapshot.take_profit.broker_order_id,
                )
            if snapshot is None and not generic_matches and _is_settled(case):
                return PaperMutationRecoveryResult(
                    case.stored_intent.mutation_key,
                    PaperMutationRecoveryState.ABSENT,
                    None,
                )
            return _unresolved(case)
        case PaperCancelOrderMutationLookup() | PaperEntryOrderMutationLookup():
            return _unresolved(case)
        case unreachable:
            assert_never(unreachable)


def _cancel_decision(case: PaperMutationRecoveryCase) -> PaperMutationRecoveryResult:
    intent = case.stored_intent.intent
    target = intent.broker_order_id
    if target is None:
        return _unresolved(case)
    lookups = tuple(
        lookup
        for lookup in case.snapshot.state.mutation_lookups
        if lookup.mutation_key == case.stored_intent.mutation_key
    )
    if len(lookups) != 1:
        return _unresolved(case)
    match lookups[0]:
        case PaperCancelOrderMutationLookup(
            broker_order_id=broker_order_id,
            order=order,
        ):
            if broker_order_id != target or order is None:
                return _unresolved(case)
            observed = _ObservedOrder(
                order.broker_order_id,
                order.symbol,
                order.status,
            )
        case PaperProtectiveOcoMutationLookup() | PaperEntryOrderMutationLookup():
            return _unresolved(case)
        case unreachable:
            assert_never(unreachable)
    if observed.broker_order_id != target or observed.symbol != intent.symbol:
        return _unresolved(case)
    if observed.status in _CANCEL_EFFECT_STATUSES:
        return PaperMutationRecoveryResult(
            case.stored_intent.mutation_key,
            PaperMutationRecoveryState.ACKNOWLEDGED,
            observed.broker_order_id,
        )
    if observed.status in _CANCEL_RETRYABLE_STATUSES and _is_settled(case):
        return PaperMutationRecoveryResult(
            case.stored_intent.mutation_key,
            PaperMutationRecoveryState.ABSENT,
            None,
        )
    return _unresolved(case)


def _close_decision(case: PaperMutationRecoveryCase) -> PaperMutationRecoveryResult:
    intent = case.stored_intent.intent
    side = intent.side
    quantity = intent.quantity
    if side is None or quantity is None:
        return _unresolved(case)
    orders = tuple(
        order
        for order in _simple_orders(case)
        if order.symbol == intent.symbol
        and order.side is side
        and order.quantity == quantity
        and order.limit_price is None
        and order.submitted_at is not None
        and order.submitted_at >= case.attempted.occurred_at
    )
    if len(orders) > 1:
        return _unresolved(case)
    unchanged = _position_is_unchanged(case, side, quantity)
    if len(orders) == 1:
        order = orders[0]
        if order.status in _FAILED_CLOSE_STATUSES:
            if unchanged and _is_settled(case):
                return PaperMutationRecoveryResult(
                    case.stored_intent.mutation_key,
                    PaperMutationRecoveryState.ABSENT,
                    None,
                )
            return _unresolved(case)
        return PaperMutationRecoveryResult(
            case.stored_intent.mutation_key,
            PaperMutationRecoveryState.ACKNOWLEDGED,
            order.broker_order_id,
        )
    if unchanged and _is_settled(case):
        return PaperMutationRecoveryResult(
            case.stored_intent.mutation_key,
            PaperMutationRecoveryState.ABSENT,
            None,
        )
    return _unresolved(case)


def _simple_orders(case: PaperMutationRecoveryCase):
    state = case.snapshot.state
    return (
        *state.broker_state.open_orders,
        *state.targeted_orders,
        *state.recent_orders,
    )


def _position_is_unchanged(
    case: PaperMutationRecoveryCase,
    side: PaperOrderSide,
    quantity: Decimal,
) -> bool:
    positions = tuple(
        position
        for position in case.snapshot.state.broker_state.positions
        if position.symbol == case.stored_intent.intent.symbol
    )
    expected = quantity if side is PaperOrderSide.SELL else -quantity
    return len(positions) == 1 and positions[0].quantity == expected


def _is_settled(case: PaperMutationRecoveryCase) -> bool:
    age = case.snapshot.completed_at - case.attempted.occurred_at
    return RECOVERY_SETTLE_DELAY <= age <= RECOVERY_MAX_EVIDENCE_AGE


def _unresolved(case: PaperMutationRecoveryCase) -> PaperMutationRecoveryResult:
    return PaperMutationRecoveryResult(
        case.stored_intent.mutation_key,
        PaperMutationRecoveryState.UNRESOLVED,
        None,
    )
