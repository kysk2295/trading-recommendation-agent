from __future__ import annotations

import json
from decimal import Decimal
from typing import assert_never

from trading_agent.alpaca_trade_updates import JsonValue
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.paper_account_activity_projection import (
    project_paper_activity_execution,
)
from trading_agent.paper_execution_models import (
    IntentId,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_stream_recovery_models import (
    PaperCancelOrderMutationLookup,
    PaperEntryOrderMutationLookup,
    PaperMutationRecoveryLookup,
    PaperProtectiveOcoMutationLookup,
    PaperRecoveryOrderObservation,
    PaperRecoveryOrderSource,
    PaperRecoveryState,
)


def recovery_order_observations(
    state: PaperRecoveryState,
) -> tuple[PaperRecoveryOrderObservation, ...]:
    return (
        *(
            PaperRecoveryOrderObservation(PaperRecoveryOrderSource.OPEN, order)
            for order in state.broker_state.open_orders
        ),
        *(PaperRecoveryOrderObservation(PaperRecoveryOrderSource.TARGETED, order) for order in state.targeted_orders),
        *(PaperRecoveryOrderObservation(PaperRecoveryOrderSource.RECENT, order) for order in state.recent_orders),
    )


def execution_details_are_complete(
    state: PaperRecoveryState,
    ledger: ReconciliationLedger,
) -> bool:
    states = {order_state.intent_id: order_state for order_state in ledger.order_states}
    orders = (
        *state.broker_state.open_orders,
        *state.targeted_orders,
        *state.recent_orders,
    )
    orders_by_intent = {order.client_order_id: order for order in orders}
    for order_state in ledger.order_states:
        if order_state.execution_detail_complete:
            continue
        order = orders_by_intent.get(order_state.intent_id)
        if order is None or not _activity_evidence_is_complete(order, state):
            return False
    for order in orders:
        order_state = states.get(order.client_order_id)
        expected = Decimal(0) if order_state is None else order_state.cumulative_filled_quantity
        ledger_matches = order.filled_quantity == expected
        if (
            ledger_matches
            and order.filled_quantity > 0
            and order_state is not None
            and order.filled_average_price != order_state.execution_average_price
        ):
            ledger_matches = False
        if not ledger_matches and not _activity_evidence_is_complete(order, state):
            return False
    return True


def recovery_snapshot_json(
    state: PaperRecoveryState,
    unresolved_intent_ids: frozenset[IntentId],
) -> str:
    account = state.broker_state.account
    payload: dict[str, JsonValue] = {
        "account": {
            "observed_at": account.observed_at.isoformat(),
            "status": account.status,
            "trading_blocked": account.trading_blocked,
            "equity": str(account.equity),
            "last_equity": str(account.last_equity),
            "buying_power": str(account.buying_power),
        },
        "open_orders": [_order_json(order) for order in state.broker_state.open_orders],
        "targeted_orders": [_order_json(order) for order in state.targeted_orders],
        "recent_orders": [_order_json(order) for order in state.recent_orders],
        "fill_activities": [
            {
                "activity_id": activity.activity_id,
                "broker_order_id": activity.broker_order_id,
                "symbol": activity.symbol,
                "side": activity.side.value,
                "event_type": activity.event_type.value,
                "quantity": str(activity.quantity),
                "cumulative_quantity": str(activity.cumulative_quantity),
                "leaves_quantity": str(activity.leaves_quantity),
                "price": str(activity.price),
                "transaction_time": activity.transaction_time.isoformat(),
            }
            for activity in state.activities
        ],
        "positions": [_position_json(position) for position in state.broker_state.positions],
        "mutation_lookups": [_mutation_lookup_json(lookup) for lookup in state.mutation_lookups],
        "unresolved_intent_ids": [str(intent_id) for intent_id in sorted(unresolved_intent_ids)],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _activity_evidence_is_complete(
    order: PaperOrderSnapshot,
    state: PaperRecoveryState,
) -> bool:
    activities = tuple(activity for activity in state.activities if activity.broker_order_id == order.broker_order_id)
    return project_paper_activity_execution(order, activities).complete


def _order_json(order: PaperOrderSnapshot) -> dict[str, JsonValue]:
    return {
        "broker_order_id": order.broker_order_id,
        "client_order_id": order.client_order_id,
        "symbol": order.symbol,
        "side": order.side.value,
        "status": order.status,
        "quantity": str(order.quantity),
        "filled_quantity": str(order.filled_quantity),
        "filled_average_price": (None if order.filled_average_price is None else str(order.filled_average_price)),
        "limit_price": None if order.limit_price is None else str(order.limit_price),
        "time_in_force": order.time_in_force,
        "extended_hours": order.extended_hours,
        "created_at": None if order.created_at is None else order.created_at.isoformat(),
        "updated_at": None if order.updated_at is None else order.updated_at.isoformat(),
        "submitted_at": (None if order.submitted_at is None else order.submitted_at.isoformat()),
        "filled_at": None if order.filled_at is None else order.filled_at.isoformat(),
        "canceled_at": (None if order.canceled_at is None else order.canceled_at.isoformat()),
        "failed_at": None if order.failed_at is None else order.failed_at.isoformat(),
        "replaced_at": (None if order.replaced_at is None else order.replaced_at.isoformat()),
        "replaced_by_order_id": order.replaced_by_order_id,
        "replaces_order_id": order.replaces_order_id,
    }


def _position_json(position: PaperPositionSnapshot) -> dict[str, JsonValue]:
    return {
        "symbol": position.symbol,
        "quantity": str(position.quantity),
        "market_value": str(position.market_value),
    }


def _mutation_lookup_json(
    lookup: PaperMutationRecoveryLookup,
) -> dict[str, JsonValue]:
    match lookup:
        case PaperProtectiveOcoMutationLookup(
            mutation_key=mutation_key,
            observed_at=observed_at,
            snapshot=snapshot,
        ):
            return {
                "kind": "protective_oco_by_client_id",
                "mutation_key": mutation_key,
                "observed_at": observed_at.isoformat(),
                "take_profit_order": (
                    None
                    if snapshot is None
                    else {
                        "broker_order_id": snapshot.take_profit.broker_order_id,
                        "client_order_id": snapshot.take_profit.client_order_id,
                        "symbol": snapshot.take_profit.symbol,
                        "status": snapshot.take_profit.status,
                    }
                ),
                "stop_order": (
                    None
                    if snapshot is None
                    else {
                        "broker_order_id": snapshot.stop_loss.broker_order_id,
                        "client_order_id": snapshot.stop_loss.client_order_id,
                        "symbol": snapshot.stop_loss.symbol,
                        "status": snapshot.stop_loss.status,
                    }
                ),
            }
        case PaperCancelOrderMutationLookup(
            mutation_key=mutation_key,
            observed_at=observed_at,
            broker_order_id=broker_order_id,
            order=order,
        ):
            return {
                "kind": "cancel_target_by_broker_id",
                "mutation_key": mutation_key,
                "observed_at": observed_at.isoformat(),
                "broker_order_id": broker_order_id,
                "order": None if order is None else _order_json(order),
            }
        case PaperEntryOrderMutationLookup(
            mutation_key=mutation_key,
            observed_at=observed_at,
            client_order_id=client_order_id,
            order=order,
        ):
            return {
                "kind": "entry_target_by_client_id",
                "mutation_key": mutation_key,
                "observed_at": observed_at.isoformat(),
                "client_order_id": client_order_id,
                "order": None if order is None else _order_json(order),
            }
        case unreachable:
            assert_never(unreachable)
