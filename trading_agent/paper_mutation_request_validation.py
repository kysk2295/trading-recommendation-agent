from __future__ import annotations

import math
from typing import override

from trading_agent.paper_execution_models import SizedPaperOrder
from trading_agent.paper_protective_oco_models import ProtectiveOcoExitPlan
from trading_agent.paper_safety_models import PaperCancelOrderAction, PaperClosePositionAction


class InvalidPaperMutationRequestError(ValueError):
    __slots__ = ("operation",)

    def __init__(self, operation: str) -> None:
        super().__init__()
        self.operation = operation

    @override
    def __str__(self) -> str:
        return f"유효하지 않은 Alpaca Paper mutation 요청입니다: {self.operation}"


def require_oco_plan(plan: ProtectiveOcoExitPlan) -> None:
    prices = (plan.take_profit_limit, plan.stop_price)
    correctly_ordered = (
        plan.take_profit_limit > plan.stop_price
        if plan.side.value == "sell"
        else plan.take_profit_limit < plan.stop_price
    )
    if (
        not plan.client_order_id
        or len(plan.client_order_id) > 48
        or not _valid_symbol(plan.symbol)
        or plan.quantity <= 0
        or any(not price.is_finite() or price <= 0 for price in prices)
        or not correctly_ordered
    ):
        raise InvalidPaperMutationRequestError("보호 OCO")


def require_entry_order(order: SizedPaperOrder) -> None:
    intent = order.intent
    prices = (
        intent.entry_limit,
        intent.stop,
        intent.target_1r,
        intent.target_2r,
        order.risk_per_share,
        order.planned_risk,
        order.notional,
    )
    if (
        not intent.intent_id
        or len(intent.intent_id) > 128
        or not _valid_symbol(intent.symbol)
        or order.quantity <= 0
        or any(not math.isfinite(value) or value <= 0 for value in prices)
        or abs(order.notional - intent.entry_limit * order.quantity) > 1e-6
    ):
        raise InvalidPaperMutationRequestError("진입 주문")


def require_cancel_action(action: PaperCancelOrderAction) -> None:
    if (
        not action.broker_order_id
        or not all(character.isalnum() or character == "-" for character in action.broker_order_id)
        or not _valid_symbol(action.symbol)
    ):
        raise InvalidPaperMutationRequestError("주문 취소")


def require_close_action(action: PaperClosePositionAction) -> None:
    if (
        not _valid_symbol(action.symbol)
        or not action.quantity.is_finite()
        or action.quantity <= 0
        or action.quantity != action.quantity.to_integral_value()
    ):
        raise InvalidPaperMutationRequestError("포지션 평탄화")


def _valid_symbol(symbol: str) -> bool:
    return bool(symbol) and symbol == symbol.upper() and len(symbol) <= 16
