from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import assert_never

from trading_agent.paper_execution_models import AccountFingerprint, SizedPaperOrder
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationIntent,
    PaperMutationOperation,
)
from trading_agent.paper_mutation_requests import (
    cancel_order_request,
    close_position_request,
    entry_order_request,
    protective_oco_request,
)
from trading_agent.paper_mutation_validation import InvalidPaperMutationRecordError
from trading_agent.paper_protective_oco_lifecycle import ProtectiveOcoResizeCancelPlan
from trading_agent.paper_protective_oco_store import StoredProtectiveOcoPlan
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyAction,
)
from trading_agent.paper_safety_store import StoredPaperSafetyPlan


def entry_order_mutation_intent(
    account_fingerprint: AccountFingerprint,
    order: SizedPaperOrder,
) -> PaperMutationIntent:
    intent = order.intent
    return PaperMutationIntent(
        account_fingerprint=account_fingerprint,
        created_at=intent.created_at,
        operation=PaperMutationOperation.SUBMIT_ENTRY,
        protective_plan_key=None,
        safety_plan_key=None,
        action_sequence=None,
        request_sha256=entry_order_request(order).sha256,
        symbol=intent.symbol,
        broker_order_id=None,
        side=intent.side,
        quantity=Decimal(order.quantity),
        entry_intent_id=intent.intent_id,
    )


def protective_oco_mutation_intent(
    account_fingerprint: AccountFingerprint,
    stored: StoredProtectiveOcoPlan,
) -> PaperMutationIntent:
    plan = stored.plan
    return PaperMutationIntent(
        account_fingerprint,
        dt.datetime.fromisoformat(stored.planned_at),
        PaperMutationOperation.SUBMIT_PROTECTIVE_OCO,
        stored.plan_key,
        None,
        None,
        protective_oco_request(plan).sha256,
        plan.symbol,
        None,
        plan.side,
        Decimal(plan.quantity),
    )


def protective_oco_cancel_mutation_intent(
    account_fingerprint: AccountFingerprint,
    stored: StoredProtectiveOcoPlan,
    cancel_plan: ProtectiveOcoResizeCancelPlan,
) -> PaperMutationIntent:
    if (
        cancel_plan.parent_intent_id != stored.plan.parent_intent_id
        or cancel_plan.source_plan_key != stored.plan_key
        or cancel_plan.symbol != stored.plan.symbol
    ):
        raise InvalidPaperMutationRecordError
    action = PaperCancelOrderAction(
        cancel_plan.broker_order_id,
        cancel_plan.symbol,
        True,
    )
    return PaperMutationIntent(
        account_fingerprint,
        dt.datetime.fromisoformat(stored.planned_at),
        PaperMutationOperation.CANCEL_PROTECTIVE_OCO,
        cancel_plan.source_plan_key,
        None,
        None,
        cancel_order_request(action).sha256,
        cancel_plan.symbol,
        cancel_plan.broker_order_id,
        None,
        None,
    )


def safety_action_mutation_intent(
    stored: StoredPaperSafetyPlan,
    sequence: int,
    action: PaperSafetyAction,
) -> PaperMutationIntent:
    plan = stored.plan
    match action:
        case PaperCancelOrderAction():
            operation = PaperMutationOperation.CANCEL_ORDER
            request_sha256 = cancel_order_request(action).sha256
            broker_order_id = action.broker_order_id
            side = None
            quantity = None
        case PaperClosePositionAction():
            operation = PaperMutationOperation.CLOSE_POSITION
            request_sha256 = close_position_request(action).sha256
            broker_order_id = None
            side = action.side
            quantity = action.quantity
        case unreachable:
            assert_never(unreachable)
    return PaperMutationIntent(
        plan.account_fingerprint,
        plan.observed_at,
        operation,
        None,
        stored.plan_key,
        sequence,
        request_sha256,
        action.symbol,
        broker_order_id,
        side,
        quantity,
    )
