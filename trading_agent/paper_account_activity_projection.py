from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_agent.paper_execution_models import (
    PaperOrderSnapshot,
    PaperTradeActivity,
    PaperTradeActivityType,
)

AVERAGE_PRICE_TOLERANCE = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class PaperActivityExecutionEvidence:
    cumulative_quantity: Decimal
    average_price: Decimal | None
    complete: bool
    reasons: tuple[str, ...]


def project_paper_activity_execution(
    order: PaperOrderSnapshot,
    activities: tuple[PaperTradeActivity, ...],
) -> PaperActivityExecutionEvidence:
    ordered = tuple(
        sorted(
            activities,
            key=lambda activity: (
                activity.transaction_time,
                activity.activity_id,
            ),
        )
    )
    reasons: list[str] = []
    cumulative = Decimal(0)
    notional = Decimal(0)
    for activity in ordered:
        cumulative += activity.quantity
        notional += activity.quantity * activity.price
        if activity.broker_order_id != order.broker_order_id:
            reasons.append("Account Activity broker order ID가 REST 주문과 다릅니다")
        if activity.symbol != order.symbol:
            reasons.append("Account Activity symbol이 REST 주문과 다릅니다")
        if activity.side is not order.side:
            reasons.append("Account Activity side가 REST 주문과 다릅니다")
        if activity.cumulative_quantity != cumulative:
            reasons.append("Account Activity 누적 체결 수량이 개별 체결 합과 다릅니다")
        if activity.leaves_quantity != order.quantity - cumulative:
            reasons.append("Account Activity 잔여 수량이 REST 주문 수량과 다릅니다")
        expected_type = (
            PaperTradeActivityType.FILL if activity.leaves_quantity == 0 else PaperTradeActivityType.PARTIAL_FILL
        )
        if activity.event_type is not expected_type:
            reasons.append("Account Activity 체결 유형과 잔여 수량이 다릅니다")
    average = None if cumulative == 0 else notional / cumulative
    if cumulative != order.filled_quantity:
        reasons.append("Account Activity 누적 체결 수량이 REST 주문과 다릅니다")
    if order.filled_quantity > 0 and not ordered:
        reasons.append("REST 체결 주문의 Account Activity가 없습니다")
    if (
        average is not None
        and order.filled_average_price is not None
        and abs(average - order.filled_average_price) > AVERAGE_PRICE_TOLERANCE
    ):
        reasons.append("Account Activity 체결 평균가격이 REST 주문과 다릅니다")
    if order.filled_quantity > 0 and order.filled_average_price is None:
        reasons.append("REST 체결 주문의 평균가격이 없습니다")
    return PaperActivityExecutionEvidence(
        cumulative_quantity=cumulative,
        average_price=average,
        complete=not reasons,
        reasons=tuple(sorted(set(reasons))),
    )
