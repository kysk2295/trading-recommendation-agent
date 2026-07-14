from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import assert_never

from trading_agent.broker_order_projection import BrokerOrderLedgerState
from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import (
    IntentId,
    PaperBrokerState,
    PaperOrderSide,
    PaperPositionSnapshot,
)
from trading_agent.paper_order_gate_models import (
    CompletePaperPortfolio,
    IncompletePaperPortfolio,
    PaperExposureKind,
    PaperPortfolioSnapshot,
)
from trading_agent.paper_protective_oco_models import (
    ProtectiveOcoClientOrderId,
    ProtectiveOcoExitPlan,
    ProtectiveOcoSnapshot,
)
from trading_agent.paper_protective_oco_store import StoredProtectiveOcoPlan

_COVERING_ORDER_STATUSES = frozenset({"new", "accepted", "pending_new", "partially_filled"})


@dataclass(frozen=True, slots=True)
class NoProtectiveExitRequired:
    parent_intent_id: IntentId


@dataclass(frozen=True, slots=True)
class BlockedProtectiveExitPlan:
    reasons: tuple[str, ...]


type ProtectiveExitPlanDecision = ProtectiveOcoExitPlan | NoProtectiveExitRequired | BlockedProtectiveExitPlan


def missing_protective_oco_reasons(
    portfolio: PaperPortfolioSnapshot,
    broker_state: PaperBrokerState,
    stored_plans: tuple[StoredProtectiveOcoPlan, ...],
) -> tuple[str, ...]:
    match portfolio:
        case IncompletePaperPortfolio():
            return ()
        case CompletePaperPortfolio(exposures=exposures):
            reasons: list[str] = []
            for exposure in exposures:
                match exposure.kind:
                    case PaperExposureKind.PENDING_ENTRY:
                        continue
                    case PaperExposureKind.PARTIAL_ENTRY | PaperExposureKind.OPEN_POSITION:
                        plans = tuple(
                            stored for stored in stored_plans if stored.plan.parent_intent_id == exposure.intent_id
                        )
                        if not plans:
                            reasons.append(f"체결 노출의 보호 OCO 계획이 원장에 없습니다: {exposure.intent_id}")
                            continue
                        plan = plans[-1].plan
                        protections = tuple(
                            snapshot
                            for snapshot in broker_state.protective_ocos
                            if snapshot.take_profit.client_order_id == plan.client_order_id
                        )
                        positions = tuple(
                            position for position in broker_state.positions if position.symbol == exposure.symbol
                        )
                        if len(protections) != 1 or len(positions) != 1:
                            reasons.append(
                                f"체결 노출의 broker 보호 OCO가 유일하게 확인되지 않았습니다: {exposure.intent_id}"
                            )
                            continue
                        if _protective_coverage_reasons(
                            plan,
                            protections[0],
                            positions[0],
                        ):
                            reasons.append(
                                f"체결 노출의 broker 보호 OCO 수량·가격·leg가 계획과 다릅니다: {exposure.intent_id}"
                            )
                    case unreachable:
                        assert_never(unreachable)
            return tuple(reasons)
        case unreachable:
            assert_never(unreachable)


def _protective_coverage_reasons(
    plan: ProtectiveOcoExitPlan,
    snapshot: ProtectiveOcoSnapshot,
    position: PaperPositionSnapshot,
) -> tuple[str, ...]:
    take_profit = snapshot.take_profit
    stop_loss = snapshot.stop_loss
    remaining_take_profit = take_profit.quantity - take_profit.filled_quantity
    remaining_stop_loss = stop_loss.quantity - stop_loss.filled_quantity
    expected_quantity = abs(position.quantity)
    match plan.side:
        case PaperOrderSide.SELL:
            position_side_is_valid = position.quantity > 0
        case PaperOrderSide.BUY:
            position_side_is_valid = position.quantity < 0
        case unreachable:
            assert_never(unreachable)
    values_are_valid = (
        snapshot.observed_at.tzinfo is not None
        and snapshot.observed_at.utcoffset() is not None
        and position_side_is_valid
        and position.symbol == plan.symbol
        and plan.quantity == expected_quantity
        and remaining_take_profit == expected_quantity
        and remaining_stop_loss == expected_quantity
        and take_profit.broker_order_id != stop_loss.broker_order_id
        and take_profit.client_order_id == plan.client_order_id
        and bool(stop_loss.client_order_id)
        and take_profit.symbol == plan.symbol == stop_loss.symbol
        and take_profit.side is plan.side is stop_loss.side
        and take_profit.status in _COVERING_ORDER_STATUSES
        and stop_loss.status in _COVERING_ORDER_STATUSES
        and take_profit.limit_price == plan.take_profit_limit
        and take_profit.stop_price is None
        and stop_loss.limit_price is None
        and stop_loss.stop_price == plan.stop_price
        and take_profit.time_in_force == plan.time_in_force
        and stop_loss.time_in_force == plan.time_in_force
        and not take_profit.extended_hours
        and not stop_loss.extended_hours
    )
    return () if values_are_valid else ("보호 OCO coverage 불일치",)


def plan_protective_oco_exit(
    intent: StoredIntent,
    order_state: BrokerOrderLedgerState,
    position: PaperPositionSnapshot | None,
) -> ProtectiveExitPlanDecision:
    reasons = list(order_state.anomaly_reasons)
    filled = order_state.cumulative_filled_quantity
    match intent.side:
        case PaperOrderSide.BUY:
            exit_side = PaperOrderSide.SELL
            expected_position = filled
        case PaperOrderSide.SELL:
            exit_side = PaperOrderSide.BUY
            expected_position = -filled
        case unreachable:
            assert_never(unreachable)
    if order_state.intent_id != intent.intent_id:
        reasons.append("보호 대상 intent와 entry 원장이 다릅니다")
    if filled < 0:
        reasons.append("entry 누적 체결 수량이 음수입니다")
    if filled > 0 and not order_state.execution_detail_complete:
        reasons.append("entry execution 상세가 불완전합니다")
    if filled == 0 and position is None and not reasons:
        return NoProtectiveExitRequired(intent.intent_id)
    if position is None:
        reasons.append("체결 원장에 대응하는 broker 포지션이 없습니다")
    else:
        if position.symbol != intent.symbol:
            reasons.append("broker 포지션 symbol이 entry intent와 다릅니다")
        if position.quantity != expected_position:
            reasons.append("broker 포지션 수량이 entry 누적 체결과 다릅니다")
        if abs(position.quantity) != abs(position.quantity).to_integral_value():
            reasons.append("초기 Paper pilot은 정수 주식 수량만 보호합니다")
    if not _prices_are_valid(intent):
        reasons.append("보호 손절·2R 목표 가격 순서가 올바르지 않습니다")
    if reasons:
        return BlockedProtectiveExitPlan(tuple(sorted(set(reasons))))
    if position is None:
        return BlockedProtectiveExitPlan(("broker 포지션을 확인하지 못했습니다",))
    return ProtectiveOcoExitPlan(
        client_order_id=_protective_client_order_id(intent.intent_id),
        parent_intent_id=intent.intent_id,
        symbol=intent.symbol,
        side=exit_side,
        quantity=int(abs(position.quantity)),
        take_profit_limit=intent.target_2r,
        stop_price=intent.stop,
    )


def _prices_are_valid(intent: StoredIntent) -> bool:
    match intent.side:
        case PaperOrderSide.BUY:
            return 0 < intent.stop < intent.entry_limit < intent.target_2r
        case PaperOrderSide.SELL:
            return 0 < intent.target_2r < intent.entry_limit < intent.stop
        case unreachable:
            assert_never(unreachable)


def _protective_client_order_id(
    parent_intent_id: IntentId,
) -> ProtectiveOcoClientOrderId:
    digest = hashlib.sha256(parent_intent_id.encode()).hexdigest()[:40]
    return ProtectiveOcoClientOrderId(f"protect-{digest}")
