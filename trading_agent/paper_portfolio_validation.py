from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final

from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import (
    PaperBrokerState,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.us_equity_calendar import NEW_YORK

ACTIVE_ENTRY_STATUSES: Final = frozenset(
    {
        "accepted",
        "accepted_for_bidding",
        "calculated",
        "new",
        "partially_filled",
        "pending_cancel",
        "pending_new",
        "pending_replace",
        "stopped",
        "suspended",
    }
)


def valid_entry_order(order: PaperOrderSnapshot, intent: StoredIntent) -> bool:
    limit = order.limit_price
    return bool(
        order.status in ACTIVE_ENTRY_STATUSES
        and order.symbol == intent.symbol
        and order.side is intent.side
        and order.quantity == Decimal(intent.quantity)
        and limit is not None
        and limit == intent.entry_limit
        and limit.is_finite()
        and limit > 0
        and order.filled_quantity.is_finite()
        and order.filled_quantity == order.filled_quantity.to_integral_value()
        and 0 <= order.filled_quantity < order.quantity
        and order.time_in_force == "day"
        and not order.extended_hours
    )


def positions_by_symbol(
    positions: tuple[PaperPositionSnapshot, ...],
    reasons: list[str],
) -> dict[str, PaperPositionSnapshot]:
    result: dict[str, PaperPositionSnapshot] = {}
    for position in positions:
        if (
            position.symbol in result
            or not position.symbol
            or position.symbol != position.symbol.upper()
            or not position.quantity.is_finite()
            or position.quantity == 0
            or position.quantity != position.quantity.to_integral_value()
            or not position.market_value.is_finite()
            or position.market_value == 0
            or (position.quantity > 0) != (position.market_value > 0)
        ):
            reasons.append(f"포지션 응답이 불완전합니다: {position.symbol}")
            continue
        result[position.symbol] = position
    return result


def position_matches_fill(
    position: PaperPositionSnapshot,
    order: PaperOrderSnapshot,
) -> bool:
    expected = (
        order.filled_quantity
        if order.side is PaperOrderSide.BUY
        else -order.filled_quantity
    )
    return position.quantity == expected


def position_matches_current_intent(
    position: PaperPositionSnapshot,
    intent: StoredIntent,
    observed_at: dt.datetime,
) -> bool:
    if not valid_stored_intent(intent, observed_at):
        return False
    expected_quantity = Decimal(intent.quantity)
    if intent.side is PaperOrderSide.SELL:
        expected_quantity = -expected_quantity
    return position.symbol == intent.symbol and position.quantity == expected_quantity


def valid_account_snapshot(state: PaperBrokerState) -> bool:
    account = state.account
    money = (account.equity, account.last_equity, account.buying_power)
    return bool(
        _is_aware(account.observed_at)
        and all(value.is_finite() for value in money)
        and account.equity > 0
        and account.last_equity > 0
        and account.buying_power >= 0
    )


def valid_stored_intent(
    intent: StoredIntent,
    observed_at: dt.datetime,
) -> bool:
    try:
        created_at = dt.datetime.fromisoformat(intent.created_at)
    except ValueError:
        return False
    prices = (
        intent.entry_limit,
        intent.stop,
        intent.target_1r,
        intent.target_2r,
    )
    if (
        not _is_aware(created_at)
        or not _is_aware(observed_at)
        or created_at > observed_at
        or created_at.astimezone(NEW_YORK).date()
        != observed_at.astimezone(NEW_YORK).date()
        or not intent.symbol
        or intent.symbol != intent.symbol.upper()
        or intent.quantity <= 0
        or not all(value.is_finite() and value > 0 for value in prices)
    ):
        return False
    if intent.side is PaperOrderSide.BUY:
        return intent.stop < intent.entry_limit < intent.target_1r < intent.target_2r
    return intent.target_2r < intent.target_1r < intent.entry_limit < intent.stop


def _is_aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
