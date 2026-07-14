from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import assert_never

from trading_agent.paper_execution_models import AccountFingerprint
from trading_agent.paper_mutation_ledger_models import (
    PaperMutationIntent,
    PaperMutationOperation,
)
from trading_agent.paper_mutation_requests import (
    cancel_order_request,
    close_position_request,
    protective_oco_request,
)
from trading_agent.paper_protective_oco_store import StoredProtectiveOcoPlan
from trading_agent.paper_safety_models import (
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyAction,
)
from trading_agent.paper_safety_store import StoredPaperSafetyPlan


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
