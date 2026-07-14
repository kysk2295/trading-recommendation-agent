from __future__ import annotations

from decimal import Decimal
from typing import assert_never

from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import (
    IntentId,
    PaperBrokerState,
    PaperOrderSide,
    PaperOrderSnapshot,
    PaperPositionSnapshot,
)
from trading_agent.paper_order_gate_models import (
    CompletePaperPortfolio,
    IncompletePaperPortfolio,
    PaperExposureKind,
    PaperPortfolioExposure,
    PaperPortfolioSnapshot,
)
from trading_agent.paper_portfolio_validation import (
    position_matches_current_intent,
    position_matches_fill,
    positions_by_symbol,
    valid_account_snapshot,
    valid_entry_order,
    valid_stored_intent,
)
from trading_agent.paper_risk import (
    BASIS_POINT_DENOMINATOR,
    DEFAULT_PAPER_RISK_CONFIG,
    PaperRiskConfig,
)


def build_paper_portfolio(
    state: PaperBrokerState,
    stored_intents: tuple[StoredIntent, ...],
    filled_intent_ids: frozenset[IntentId],
    config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
) -> PaperPortfolioSnapshot:
    config.assert_within_hard_limits()
    reasons: list[str] = []
    if not valid_account_snapshot(state):
        return IncompletePaperPortfolio(("Paper 계좌 위험값이 불완전합니다",))
    intents_by_id = {intent.intent_id: intent for intent in stored_intents}
    if len(intents_by_id) != len(stored_intents):
        reasons.append("원장에 중복 intent ID가 있습니다")
    positions_by_symbol_map = positions_by_symbol(state.positions, reasons)
    exposures: list[PaperPortfolioExposure] = []
    consumed_positions: set[str] = set()
    consumed_intents: set[str] = set()
    reserved_risk = _reserved_slot_risk(state, config)

    for order in state.open_orders:
        intent = intents_by_id.get(order.client_order_id)
        if intent is None:
            reasons.append(f"원장에 없는 미체결 주문: {order.client_order_id}")
            continue
        if not valid_stored_intent(intent, state.account.observed_at):
            reasons.append(f"활성 원장 intent가 불완전합니다: {intent.intent_id}")
            continue
        exposure = _order_exposure(
            order,
            intent,
            positions_by_symbol_map,
            _planned_exposure_risk(intent, reserved_risk, config),
            consumed_positions,
            reasons,
        )
        if exposure is not None:
            if intent.intent_id in consumed_intents:
                reasons.append(f"중복된 활성 intent: {intent.intent_id}")
            consumed_intents.add(intent.intent_id)
            exposures.append(exposure)

    for symbol, position in positions_by_symbol_map.items():
        if symbol in consumed_positions:
            continue
        matches = tuple(
            intent
            for intent in stored_intents
            if intent.intent_id in filled_intent_ids
            and position_matches_current_intent(
                position,
                intent,
                state.account.observed_at,
            )
        )
        if len(matches) != 1:
            reasons.append(f"열린 포지션의 현재 원장 intent가 유일하지 않습니다: {symbol}")
            continue
        intent = matches[0]
        if intent.intent_id in consumed_intents:
            reasons.append(f"중복된 활성 intent: {intent.intent_id}")
            continue
        consumed_intents.add(intent.intent_id)
        exposures.append(
            PaperPortfolioExposure(
                intent_id=intent.intent_id,
                symbol=symbol,
                kind=PaperExposureKind.OPEN_POSITION,
                gross_exposure=max(
                    abs(position.market_value),
                    abs(position.quantity) * intent.entry_limit,
                ),
                planned_risk=_planned_exposure_risk(
                    intent,
                    reserved_risk,
                    config,
                ),
            )
        )

    symbols = tuple(exposure.symbol for exposure in exposures)
    if len(set(symbols)) != len(symbols):
        reasons.append("동일 종목에 둘 이상의 활성 위험 노출이 있습니다")
    if reasons:
        return IncompletePaperPortfolio(tuple(sorted(set(reasons))))
    account = state.account
    return CompletePaperPortfolio(
        observed_at=account.observed_at,
        account_status=account.status,
        trading_blocked=account.trading_blocked,
        equity=account.equity,
        last_equity=account.last_equity,
        buying_power=account.buying_power,
        exposures=tuple(exposures),
    )


def _order_exposure(
    order: PaperOrderSnapshot,
    intent: StoredIntent,
    positions: dict[str, PaperPositionSnapshot],
    reserved_risk: Decimal,
    consumed_positions: set[str],
    reasons: list[str],
) -> PaperPortfolioExposure | None:
    if not valid_entry_order(order, intent):
        reasons.append(f"미체결 주문의 수량·가격·상태가 불완전합니다: {order.client_order_id}")
        return None
    limit_price = order.limit_price
    if limit_price is None:
        reasons.append(f"미체결 주문 가격이 없습니다: {order.client_order_id}")
        return None
    remaining = order.quantity - order.filled_quantity
    position = positions.get(order.symbol)
    gross = remaining * limit_price
    if order.filled_quantity == 0:
        if position is not None:
            reasons.append(f"미체결 수량 0인 주문과 포지션이 동시에 존재합니다: {order.symbol}")
            return None
        kind = PaperExposureKind.PENDING_ENTRY
    else:
        if position is None or not position_matches_fill(position, order):
            reasons.append(f"부분체결 주문과 포지션 수량이 일치하지 않습니다: {order.symbol}")
            return None
        consumed_positions.add(order.symbol)
        gross += max(
            abs(position.market_value),
            abs(position.quantity) * limit_price,
        )
        kind = PaperExposureKind.PARTIAL_ENTRY
    return PaperPortfolioExposure(
        intent_id=intent.intent_id,
        symbol=order.symbol,
        kind=kind,
        gross_exposure=gross,
        planned_risk=reserved_risk,
    )


def _reserved_slot_risk(
    state: PaperBrokerState,
    config: PaperRiskConfig,
) -> Decimal:
    equity = min(
        state.account.equity,
        state.account.last_equity,
        Decimal(str(config.reference_equity)),
    )
    return min(
        Decimal(str(config.max_risk_dollars)),
        equity * Decimal(str(config.risk_fraction)),
    )


def _planned_exposure_risk(
    intent: StoredIntent,
    reserved_risk: Decimal,
    config: PaperRiskConfig,
) -> Decimal:
    match intent.side:
        case PaperOrderSide.BUY:
            stop_distance = intent.entry_limit - intent.stop
        case PaperOrderSide.SELL:
            stop_distance = intent.stop - intent.entry_limit
        case unreachable:
            assert_never(unreachable)
    minimum_cost = (
        (intent.entry_limit + intent.stop)
        * Decimal(str(config.per_side_cost_bps))
        / BASIS_POINT_DENOMINATOR
    )
    actual_risk = (stop_distance + minimum_cost) * Decimal(intent.quantity)
    return max(reserved_risk, actual_risk)
