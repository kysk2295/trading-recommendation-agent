from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from trading_agent.intraday_broker_shadow_models import (
    BrokerShadowEvidenceRequest,
    BrokerShadowTradePair,
    InvalidBrokerShadowEvidenceError,
)
from trading_agent.metrics import NEW_YORK, PaperTrade, net_return
from trading_agent.paper_execution_models import (
    BrokerOrderId,
    IntentId,
    PaperOrderSide,
)
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEventType,
    PaperMutationOperation,
)

if TYPE_CHECKING:
    from trading_agent.broker_order_projection import BrokerOrderLedgerState
    from trading_agent.execution_schema import StoredIntent
    from trading_agent.paper_account_activity_store import StoredPaperAccountActivity


@dataclass(frozen=True, slots=True)
class BrokerShadowPairing:
    pairs: tuple[BrokerShadowTradePair, ...]
    unpaired_broker_intent_count: int


def pair_broker_shadow_trades(
    request: BrokerShadowEvidenceRequest,
) -> BrokerShadowPairing:
    shadow_by_id = {trade.recommendation_id: trade for trade in request.shadow_trades}
    if len(shadow_by_id) != len(request.shadow_trades):
        raise InvalidBrokerShadowEvidenceError
    states = {state.intent_id: state for state in request.ledger.order_states}
    if len(states) != len(request.ledger.order_states):
        raise InvalidBrokerShadowEvidenceError
    pairs: list[BrokerShadowTradePair] = []
    unpaired = 0
    for intent in request.ledger.intents:
        if intent.strategy_version != request.strategy_version:
            continue
        shadow = shadow_by_id.get(intent.intent_id)
        state = states.get(intent.intent_id)
        if shadow is None or state is None or not _entry_is_pairable(intent, state, shadow):
            unpaired += 1
            continue
        execution_average_price = state.execution_average_price
        if execution_average_price is None:
            unpaired += 1
            continue
        exit_activities = _exit_activities(request, intent.intent_id, shadow)
        exit_quantity = sum(
            (stored.activity.quantity for stored in exit_activities),
            start=Decimal(0),
        )
        if not exit_activities or exit_quantity != state.cumulative_filled_quantity:
            unpaired += 1
            continue
        exit_notional = sum(
            (
                stored.activity.quantity * stored.activity.price
                for stored in exit_activities
            ),
            start=Decimal(0),
        )
        broker_entry = float(execution_average_price)
        broker_exit = float(exit_notional / exit_quantity)
        broker_trade = PaperTrade(
            shadow.recommendation_id,
            shadow.symbol,
            shadow.strategy,
            shadow.entry_at,
            max(stored.activity.transaction_time for stored in exit_activities),
            broker_entry,
            broker_exit,
            broker_exit / broker_entry - 1.0,
            shadow.exit_state,
            False,
        )
        broker_net = net_return(broker_trade, 20)
        shadow_net = net_return(shadow, 20)
        pairs.append(
            BrokerShadowTradePair(
                recommendation_id=shadow.recommendation_id,
                session_date=shadow.exit_at.astimezone(NEW_YORK).date(),
                symbol=shadow.symbol,
                strategy_version=intent.strategy_version,
                broker_entry=broker_entry,
                broker_exit=broker_exit,
                shadow_entry=shadow.entry,
                shadow_exit=shadow.exit,
                broker_net_return=broker_net,
                shadow_net_return=shadow_net,
                return_difference=broker_net - shadow_net,
            )
        )
    return BrokerShadowPairing(
        tuple(sorted(pairs, key=lambda pair: (pair.session_date, pair.recommendation_id))),
        unpaired,
    )


def _entry_is_pairable(
    intent: StoredIntent,
    state: BrokerOrderLedgerState,
    shadow: PaperTrade,
) -> bool:
    try:
        created_at = dt.datetime.fromisoformat(intent.created_at)
    except ValueError:
        return False
    return (
        created_at.tzinfo is not None
        and created_at.utcoffset() is not None
        and created_at.astimezone(NEW_YORK).date()
        == shadow.entry_at.astimezone(NEW_YORK).date()
        and intent.intent_id == shadow.recommendation_id
        and intent.symbol == shadow.symbol
        and intent.strategy_id == shadow.strategy
        and intent.side is PaperOrderSide.BUY
        and state.complete_fill
        and state.execution_detail_complete
        and not state.anomaly_reasons
        and state.execution_average_price is not None
        and state.execution_average_price > 0
    )


def _exit_activities(
    request: BrokerShadowEvidenceRequest,
    intent_id: IntentId,
    shadow: PaperTrade,
) -> tuple[StoredPaperAccountActivity, ...]:
    plan_keys = {
        stored.plan_key
        for stored in request.ledger.protective_oco_plans
        if stored.plan.parent_intent_id == intent_id
    }
    exit_order_ids = {
        leg.broker_order_id
        for stored in request.protective_oco_snapshots
        if stored.plan_key in plan_keys
        for leg in (stored.snapshot.take_profit, stored.snapshot.stop_loss)
    }
    exit_order_ids.update(_close_order_ids(request, shadow))
    unique = {
        stored.activity.activity_id: stored
        for stored in request.account_activities
        if stored.activity.broker_order_id in exit_order_ids
        and stored.activity.symbol == shadow.symbol
        and stored.activity.side is PaperOrderSide.SELL
        and stored.activity.transaction_time.astimezone(NEW_YORK).date()
        == shadow.exit_at.astimezone(NEW_YORK).date()
    }
    return tuple(
        sorted(
            unique.values(),
            key=lambda stored: (
                stored.activity.transaction_time,
                stored.activity.activity_id,
            ),
        )
    )


def _close_order_ids(
    request: BrokerShadowEvidenceRequest,
    shadow: PaperTrade,
) -> set[BrokerOrderId]:
    mutation_keys = {
        stored.mutation_key
        for stored in request.ledger.paper_mutation_intents
        if stored.intent.operation is PaperMutationOperation.CLOSE_POSITION
        and stored.intent.symbol == shadow.symbol
        and stored.intent.side is PaperOrderSide.SELL
        and stored.intent.created_at.astimezone(NEW_YORK).date()
        == shadow.exit_at.astimezone(NEW_YORK).date()
    }
    acknowledged = frozenset(
        (
            PaperMutationEventType.ACKNOWLEDGED,
            PaperMutationEventType.RECOVERED_ACKNOWLEDGED,
        )
    )
    return {
        stored.event.broker_order_id
        for stored in request.ledger.paper_mutation_events
        if stored.mutation_key in mutation_keys
        and stored.event.event_type in acknowledged
        and stored.event.broker_order_id is not None
        and stored.event.occurred_at.astimezone(NEW_YORK).date()
        == shadow.exit_at.astimezone(NEW_YORK).date()
    }


__all__ = ("BrokerShadowPairing", "pair_broker_shadow_trades")
