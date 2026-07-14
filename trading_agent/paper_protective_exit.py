from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal, NewType, assert_never

from trading_agent.broker_order_projection import BrokerOrderLedgerState
from trading_agent.execution_schema import StoredIntent
from trading_agent.paper_execution_models import (
    IntentId,
    PaperOrderSide,
    PaperPositionSnapshot,
)
from trading_agent.paper_order_gate_models import (
    CompletePaperPortfolio,
    IncompletePaperPortfolio,
    PaperExposureKind,
    PaperPortfolioSnapshot,
)

ProtectiveOcoClientOrderId = NewType("ProtectiveOcoClientOrderId", str)


@dataclass(frozen=True, slots=True)
class ProtectiveOcoExitPlan:
    client_order_id: ProtectiveOcoClientOrderId
    parent_intent_id: IntentId
    symbol: str
    side: PaperOrderSide
    quantity: int
    take_profit_limit: Decimal
    stop_price: Decimal
    order_class: Literal["oco"] = field(init=False, default="oco")
    order_type: Literal["limit"] = field(init=False, default="limit")
    time_in_force: Literal["day"] = field(init=False, default="day")
    extended_hours: Literal[False] = field(init=False, default=False)


@dataclass(frozen=True, slots=True)
class NoProtectiveExitRequired:
    parent_intent_id: IntentId


@dataclass(frozen=True, slots=True)
class BlockedProtectiveExitPlan:
    reasons: tuple[str, ...]


type ProtectiveExitPlanDecision = ProtectiveOcoExitPlan | NoProtectiveExitRequired | BlockedProtectiveExitPlan


def missing_protective_oco_reasons(
    portfolio: PaperPortfolioSnapshot,
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
                        reasons.append(f"체결 노출의 broker 보호 OCO가 아직 확인되지 않았습니다: {exposure.intent_id}")
                    case unreachable:
                        assert_never(unreachable)
            return tuple(reasons)
        case unreachable:
            assert_never(unreachable)


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
