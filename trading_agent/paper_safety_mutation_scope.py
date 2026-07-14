from __future__ import annotations

from decimal import Decimal

from trading_agent.paper_execution_models import PaperBrokerState, PaperOrderSnapshot
from trading_agent.paper_risk import PaperRiskConfig
from trading_agent.paper_safety_models import PaperSafetyPlan


def paper_safety_mutation_scope_reasons(
    broker_state: PaperBrokerState,
    plan: PaperSafetyPlan,
    config: PaperRiskConfig,
) -> tuple[str, ...]:
    if not plan.actions:
        return ()
    reasons: list[str] = []
    maximum = config.max_open_positions
    if len(broker_state.open_orders) > maximum:
        reasons.append("Paper safety mutation scope가 허용된 entry order 수를 초과합니다")
    if len(broker_state.positions) > maximum:
        reasons.append("Paper safety mutation scope가 허용된 position 수를 초과합니다")
    if len(broker_state.protective_ocos) > maximum:
        reasons.append("Paper safety mutation scope가 허용된 protective OCO 수를 초과합니다")
    symbols = {
        *(order.symbol for order in broker_state.open_orders),
        *(position.symbol for position in broker_state.positions),
        *(snapshot.take_profit.symbol for snapshot in broker_state.protective_ocos),
    }
    if len(symbols) > maximum:
        reasons.append("Paper safety mutation scope가 허용된 symbol 수를 초과합니다")
    entry_notional = _pending_entry_notional(broker_state.open_orders)
    position_values = tuple(abs(position.market_value) for position in broker_state.positions)
    if entry_notional is None:
        reasons.append("Paper safety mutation scope의 entry notional을 확정할 수 없습니다")
    if any(not value.is_finite() for value in position_values):
        reasons.append("Paper safety mutation scope의 position notional을 확정할 수 없습니다")
    if (
        entry_notional is not None
        and all(value.is_finite() for value in position_values)
        and entry_notional + sum(position_values, start=Decimal(0)) > Decimal(str(config.max_notional_dollars))
    ):
        reasons.append("Paper safety mutation scope notional이 허용 한도를 초과합니다")
    return tuple(dict.fromkeys(reasons))


def _pending_entry_notional(
    orders: tuple[PaperOrderSnapshot, ...],
) -> Decimal | None:
    total = Decimal(0)
    for order in orders:
        remaining = order.quantity - order.filled_quantity
        price = order.limit_price
        if (
            not order.quantity.is_finite()
            or not order.filled_quantity.is_finite()
            or remaining <= 0
            or price is None
            or not price.is_finite()
            or price <= 0
        ):
            return None
        total += abs(remaining * price)
    return total
