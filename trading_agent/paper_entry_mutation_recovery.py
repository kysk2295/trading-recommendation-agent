from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final, assert_never

from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_mutation_ledger_models import PaperMutationEvent
from trading_agent.paper_mutation_recovery_models import (
    PaperMutationRecoveryResult,
    PaperMutationRecoverySnapshot,
    PaperMutationRecoveryState,
)
from trading_agent.paper_mutation_store import StoredPaperMutationIntent
from trading_agent.paper_stream_recovery_models import (
    PaperCancelOrderMutationLookup,
    PaperEntryOrderMutationLookup,
    PaperProtectiveOcoMutationLookup,
)

ENTRY_RECOVERY_SETTLE_DELAY: Final = dt.timedelta(seconds=30)
ENTRY_RECOVERY_MAX_EVIDENCE_AGE: Final = dt.timedelta(days=1)


def decide_entry_mutation_recovery(
    stored: StoredPaperMutationIntent,
    attempted: PaperMutationEvent,
    snapshot: PaperMutationRecoverySnapshot,
    order_intents: tuple[StoredIntent, ...],
) -> PaperMutationRecoveryResult:
    entry_id = stored.intent.entry_intent_id
    sources = tuple(intent for intent in order_intents if intent.intent_id == entry_id)
    lookups = tuple(lookup for lookup in snapshot.state.mutation_lookups if lookup.mutation_key == stored.mutation_key)
    if entry_id is None or len(sources) != 1 or len(lookups) != 1:
        return _unresolved(stored)
    match lookups[0]:
        case PaperEntryOrderMutationLookup(
            client_order_id=client_order_id,
            order=order,
        ):
            if client_order_id != entry_id:
                return _unresolved(stored)
            if order is None:
                age = snapshot.completed_at - attempted.occurred_at
                settled = ENTRY_RECOVERY_SETTLE_DELAY <= age <= ENTRY_RECOVERY_MAX_EVIDENCE_AGE
                return (
                    PaperMutationRecoveryResult(
                        stored.mutation_key,
                        PaperMutationRecoveryState.ABSENT,
                        None,
                    )
                    if settled
                    else _unresolved(stored)
                )
            source = sources[0]
            if (
                order.client_order_id == source.intent_id
                and order.symbol == source.symbol
                and order.side is source.side
                and order.quantity == Decimal(source.quantity)
                and order.limit_price == source.entry_limit
                and order.time_in_force == "day"
                and not order.extended_hours
            ):
                return PaperMutationRecoveryResult(
                    stored.mutation_key,
                    PaperMutationRecoveryState.ACKNOWLEDGED,
                    order.broker_order_id,
                )
            return _unresolved(stored)
        case PaperProtectiveOcoMutationLookup() | PaperCancelOrderMutationLookup():
            return _unresolved(stored)
        case unreachable:
            assert_never(unreachable)


def _unresolved(stored: StoredPaperMutationIntent) -> PaperMutationRecoveryResult:
    return PaperMutationRecoveryResult(
        stored.mutation_key,
        PaperMutationRecoveryState.UNRESOLVED,
        None,
    )
