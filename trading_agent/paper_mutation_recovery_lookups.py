from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Final, assert_never

from trading_agent.alpaca_paper_client import AlpacaPaperClient
from trading_agent.alpaca_paper_order_stream import PaperOrderStreamHeartbeat
from trading_agent.execution_ledger_reader import ReconciliationLedger
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationEventType,
    PaperMutationOperation,
)
from trading_agent.paper_mutation_store import StoredPaperMutationIntent
from trading_agent.paper_runtime import MAX_RUNTIME_RECEIPT_AGE
from trading_agent.paper_stream_recovery_models import (
    PaperCancelOrderMutationLookup,
    PaperMutationRecoveryLookup,
    PaperProtectiveOcoMutationLookup,
    PaperRecoveryState,
)

_RECOVERABLE_TYPES: Final = frozenset({PaperMutationEventType.ATTEMPTED, PaperMutationEventType.AMBIGUOUS})


def read_paper_mutation_recovery_lookups(
    client: AlpacaPaperClient,
    ledger: ReconciliationLedger,
    clock: Callable[[], dt.datetime],
) -> tuple[PaperMutationRecoveryLookup, ...]:
    lookups: list[PaperMutationRecoveryLookup] = []
    for stored_intent in recoverable_paper_mutation_intents(ledger):
        intent = stored_intent.intent
        match intent.operation:
            case PaperMutationOperation.SUBMIT_PROTECTIVE_OCO:
                plans = tuple(
                    stored for stored in ledger.protective_oco_plans if stored.plan_key == intent.protective_plan_key
                )
                if len(plans) != 1:
                    continue
                snapshot = client.protective_oco_by_client_id(plans[0].plan.client_order_id)
                lookups.append(
                    PaperProtectiveOcoMutationLookup(
                        stored_intent.mutation_key,
                        clock() if snapshot is None else snapshot.observed_at,
                        snapshot,
                    )
                )
            case PaperMutationOperation.CANCEL_ORDER:
                broker_order_id = intent.broker_order_id
                if broker_order_id is None:
                    continue
                order = client.order_by_id(broker_order_id)
                lookups.append(
                    PaperCancelOrderMutationLookup(
                        stored_intent.mutation_key,
                        clock(),
                        broker_order_id,
                        order,
                    )
                )
            case PaperMutationOperation.CLOSE_POSITION:
                continue
            case unreachable:
                assert_never(unreachable)
    return tuple(lookups)


def recoverable_paper_mutation_intents(
    ledger: ReconciliationLedger,
) -> tuple[StoredPaperMutationIntent, ...]:
    pending: list[StoredPaperMutationIntent] = []
    for stored_intent in ledger.paper_mutation_intents:
        events = tuple(
            stored.event for stored in ledger.paper_mutation_events if stored.mutation_key == stored_intent.mutation_key
        )
        if events and events[-1].event_type in _RECOVERABLE_TYPES:
            pending.append(stored_intent)
    return tuple(pending)


def paper_mutation_lookup_reasons(
    before: PaperOrderStreamHeartbeat,
    after: PaperOrderStreamHeartbeat,
    state: PaperRecoveryState,
    ledger: ReconciliationLedger,
) -> tuple[str, ...]:
    required = frozenset(
        stored.mutation_key
        for stored in recoverable_paper_mutation_intents(ledger)
        if stored.intent.operation
        in {
            PaperMutationOperation.SUBMIT_PROTECTIVE_OCO,
            PaperMutationOperation.CANCEL_ORDER,
        }
    )
    keys = tuple(lookup.mutation_key for lookup in state.mutation_lookups)
    observed = frozenset(keys)
    reasons = [
        *(f"mutation targeted REST 조회 누락: {key}" for key in sorted(required - observed)),
        *(f"요청하지 않은 mutation targeted REST 조회: {key}" for key in sorted(observed - required)),
        *(f"중복 mutation targeted REST 조회: {key}" for key in sorted(observed) if keys.count(key) > 1),
    ]
    reasons.extend(
        "mutation targeted REST 수신시각이 heartbeat 복구 구간 밖입니다"
        for lookup in state.mutation_lookups
        if not before.pong_at <= lookup.observed_at <= after.pong_at
        or after.pong_at - lookup.observed_at > MAX_RUNTIME_RECEIPT_AGE
    )
    return tuple(reasons)
