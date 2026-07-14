from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final

from trading_agent.paper_execution_models import (
    PaperBrokerState,
    PaperMarketClockSnapshot,
    PaperOrderSide,
    PaperPositionSnapshot,
)
from trading_agent.paper_order_gate_models import (
    CompletePaperPortfolio,
    PaperExposureKind,
    PaperPortfolioSnapshot,
)
from trading_agent.paper_risk import DEFAULT_PAPER_RISK_CONFIG, PaperRiskConfig
from trading_agent.paper_safety_models import (
    BlockedPaperSafetyPlan,
    PaperCancelOrderAction,
    PaperClosePositionAction,
    PaperSafetyAction,
    PaperSafetyPhase,
    PaperSafetyPlan,
    PaperSafetyPlanDecision,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

CURRENT_SAFETY_SNAPSHOT_AGE: Final = dt.timedelta(seconds=5)
ENTRY_CUTOFF_BEFORE_CLOSE: Final = dt.timedelta(minutes=30)
EOD_FLATTEN_BEFORE_CLOSE: Final = dt.timedelta(minutes=5)


def plan_paper_safety_actions(
    broker_state: PaperBrokerState,
    market_clock: PaperMarketClockSnapshot,
    portfolio: PaperPortfolioSnapshot,
    evaluated_at: dt.datetime,
    config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
    *,
    kill_switch_latched: bool = False,
) -> PaperSafetyPlanDecision:
    config.assert_within_hard_limits()
    reasons = _snapshot_reasons(
        broker_state,
        market_clock,
        portfolio,
        evaluated_at,
    )
    if reasons:
        return BlockedPaperSafetyPlan(reasons)
    if not isinstance(portfolio, CompletePaperPortfolio):
        return BlockedPaperSafetyPlan(("전체 포트폴리오 위험 집계가 불완전합니다",))
    mark_to_market = portfolio.equity - portfolio.last_equity
    open_position_risk = sum(
        (
            exposure.planned_risk
            for exposure in portfolio.exposures
            if exposure.kind is not PaperExposureKind.PENDING_ENTRY
        ),
        start=Decimal(0),
    )
    conservative = mark_to_market - open_position_risk
    loss_limit = -Decimal(str(config.daily_loss_limit_dollars))
    now_new_york = evaluated_at.astimezone(NEW_YORK)
    bounds = regular_session_bounds(now_new_york.date())
    if bounds is None:
        return BlockedPaperSafetyPlan(("로컬 거래소 달력상 정규장이 아닙니다",))
    session_close = bounds[1]
    if kill_switch_latched or mark_to_market <= loss_limit or conservative <= loss_limit:
        phase = PaperSafetyPhase.KILL_SWITCH
    elif now_new_york >= session_close - EOD_FLATTEN_BEFORE_CLOSE:
        phase = PaperSafetyPhase.EOD_FLATTEN
    elif now_new_york >= session_close - ENTRY_CUTOFF_BEFORE_CLOSE:
        phase = PaperSafetyPhase.ENTRY_CUTOFF
    else:
        phase = PaperSafetyPhase.MONITORING
    actions = _actions_for_phase(phase, broker_state)
    if isinstance(actions, BlockedPaperSafetyPlan):
        return actions
    return PaperSafetyPlan(
        broker_state.account.account_fingerprint,
        evaluated_at,
        now_new_york.date(),
        phase,
        mark_to_market,
        conservative,
        actions,
    )


def _snapshot_reasons(
    state: PaperBrokerState,
    clock: PaperMarketClockSnapshot,
    portfolio: PaperPortfolioSnapshot,
    now: dt.datetime,
) -> tuple[str, ...]:
    if not isinstance(portfolio, CompletePaperPortfolio):
        return ("전체 포트폴리오 위험 집계가 불완전합니다",)
    receipts = (
        state.account.observed_at,
        clock.observed_at,
        clock.market_timestamp,
        portfolio.observed_at,
        *(snapshot.observed_at for snapshot in state.protective_ocos),
    )
    if not _is_current(now, receipts):
        return ("Paper 안전조치 snapshot이 현재 5초 구간의 완전한 응답이 아닙니다",)
    now_new_york = now.astimezone(NEW_YORK)
    bounds = regular_session_bounds(now_new_york.date())
    if (
        bounds is None
        or not clock.is_open
        or not bounds[0] <= now_new_york < bounds[1]
        or clock.next_close.astimezone(NEW_YORK) != bounds[1]
    ):
        return ("브로커와 로컬 거래소의 열린 정규장 경계가 일치하지 않습니다",)
    if (
        portfolio.account_status.upper() != "ACTIVE"
        or portfolio.trading_blocked
        or state.account.status.upper() != portfolio.account_status.upper()
        or state.account.trading_blocked != portfolio.trading_blocked
        or state.account.equity != portfolio.equity
        or state.account.last_equity != portfolio.last_equity
    ):
        return ("Paper 계좌와 포트폴리오 안전값이 일치하지 않습니다",)
    return ()


def _actions_for_phase(
    phase: PaperSafetyPhase,
    state: PaperBrokerState,
) -> tuple[PaperSafetyAction, ...] | BlockedPaperSafetyPlan:
    if phase is PaperSafetyPhase.MONITORING:
        return ()
    entry_cancels = tuple(
        PaperCancelOrderAction(order.broker_order_id, order.symbol, False)
        for order in sorted(state.open_orders, key=lambda item: item.broker_order_id)
    )
    if phase is PaperSafetyPhase.ENTRY_CUTOFF:
        return entry_cancels
    protective_cancels = tuple(
        PaperCancelOrderAction(
            snapshot.take_profit.broker_order_id,
            snapshot.take_profit.symbol,
            True,
        )
        for snapshot in sorted(
            state.protective_ocos,
            key=lambda item: item.take_profit.broker_order_id,
        )
    )
    positions = tuple(sorted(state.positions, key=lambda item: item.symbol))
    position_reasons = _position_reasons(positions)
    if position_reasons:
        return BlockedPaperSafetyPlan(position_reasons)
    closes = tuple(_close_action(position) for position in positions)
    actions = (*entry_cancels, *protective_cancels, *closes)
    cancel_ids = tuple(action.broker_order_id for action in actions if isinstance(action, PaperCancelOrderAction))
    if len(cancel_ids) != len(set(cancel_ids)):
        return BlockedPaperSafetyPlan(("취소 대상 broker order identity가 중복됩니다",))
    return actions


def _position_reasons(
    positions: tuple[PaperPositionSnapshot, ...],
) -> tuple[str, ...]:
    symbols = tuple(position.symbol for position in positions)
    if len(symbols) != len(set(symbols)):
        return ("평탄화 대상 broker 포지션 symbol이 중복됩니다",)
    if any(
        not position.symbol
        or position.symbol != position.symbol.upper()
        or not position.quantity.is_finite()
        or position.quantity == 0
        or abs(position.quantity) != abs(position.quantity).to_integral_value()
        for position in positions
    ):
        return ("초기 Paper 안전조치는 유효한 정수 주식 포지션만 평탄화합니다",)
    return ()


def _close_action(position: PaperPositionSnapshot) -> PaperClosePositionAction:
    side = PaperOrderSide.SELL if position.quantity > 0 else PaperOrderSide.BUY
    return PaperClosePositionAction(position.symbol, side, abs(position.quantity))


def _is_current(now: dt.datetime, receipts: tuple[dt.datetime, ...]) -> bool:
    if not _is_aware(now) or not all(_is_aware(value) for value in receipts):
        return False
    return all(
        dt.timedelta(0) <= now.astimezone(dt.UTC) - value.astimezone(dt.UTC) <= CURRENT_SAFETY_SNAPSHOT_AGE
        for value in receipts
    )


def _is_aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
