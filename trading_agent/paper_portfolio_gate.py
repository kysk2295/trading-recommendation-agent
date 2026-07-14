from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import Final, assert_never

from trading_agent.paper_execution_models import PaperOrderSide, SizedPaperOrder
from trading_agent.paper_order_gate_models import (
    CompletePaperPortfolio,
    IncompletePaperPortfolio,
    PaperPortfolioSnapshot,
)
from trading_agent.paper_risk import PaperRiskConfig

CURRENT_PORTFOLIO_AGE: Final = dt.timedelta(seconds=5)
MAX_SYMBOL_NOTIONAL_FRACTION: Final = Decimal("0.20")
MONEY_TOLERANCE: Final = Decimal("0.01")


def portfolio_reasons(
    portfolio_snapshot: PaperPortfolioSnapshot,
    candidate: SizedPaperOrder,
    evaluated_at: dt.datetime,
    config: PaperRiskConfig,
) -> tuple[str, ...]:
    match portfolio_snapshot:
        case IncompletePaperPortfolio():
            return ("전체 포트폴리오 위험 집계가 불완전합니다",)
        case CompletePaperPortfolio() as portfolio:
            return _complete_portfolio_reasons(
                portfolio,
                candidate,
                evaluated_at,
                config,
            )
        case unreachable:
            assert_never(unreachable)


def _complete_portfolio_reasons(
    portfolio: CompletePaperPortfolio,
    candidate: SizedPaperOrder,
    evaluated_at: dt.datetime,
    config: PaperRiskConfig,
) -> tuple[str, ...]:
    decimal_values = (
        portfolio.equity,
        portfolio.last_equity,
        portfolio.buying_power,
        portfolio.gross_exposure,
        portfolio.planned_open_risk,
    )
    candidate_values = (
        candidate.intent.entry_limit,
        candidate.intent.stop,
        candidate.intent.target_1r,
        candidate.intent.target_2r,
        candidate.risk_per_share,
        candidate.planned_risk,
        candidate.notional,
    )
    if (
        not _is_current(evaluated_at, portfolio.observed_at)
        or not all(value.is_finite() for value in decimal_values)
        or not all(math.isfinite(value) for value in candidate_values)
        or portfolio.equity <= 0
        or portfolio.last_equity <= 0
        or portfolio.buying_power < 0
        or portfolio.gross_exposure < 0
        or portfolio.planned_open_risk < 0
        or candidate.quantity <= 0
        or candidate.risk_per_share <= 0
        or candidate.planned_risk <= 0
        or candidate.notional <= 0
    ):
        return ("포트폴리오 또는 후보 위험값이 현재의 유효한 값이 아닙니다",)
    if portfolio.account_status.upper() != "ACTIVE" or portfolio.trading_blocked:
        return ("Paper 계좌가 주문 가능한 ACTIVE 상태가 아닙니다",)
    exposure_count = len(portfolio.exposures)
    aggregation_is_consistent = (
        len(portfolio.exposed_symbols) == exposure_count
        and all(
            exposure.intent_id
            and exposure.symbol
            and exposure.symbol == exposure.symbol.upper()
            and exposure.gross_exposure.is_finite()
            and exposure.gross_exposure > 0
            and exposure.planned_risk.is_finite()
            and exposure.planned_risk > 0
            for exposure in portfolio.exposures
        )
        and (
            (
                exposure_count == 0
                and portfolio.gross_exposure == 0
                and portfolio.planned_open_risk == 0
            )
            or (
                exposure_count > 0
                and portfolio.gross_exposure > 0
                and portfolio.planned_open_risk > 0
            )
        )
    )
    if not aggregation_is_consistent:
        return ("포트폴리오 종목·슬롯·예약 위험 집계가 서로 일치하지 않습니다",)
    if not _order_geometry_is_valid(candidate):
        return ("후보 주문의 진입·손절·목표·주당위험 관계가 올바르지 않습니다",)
    if candidate.intent.symbol in portfolio.exposed_symbols:
        return ("동일 종목의 기존 포지션 또는 진입대기 주문이 있습니다",)
    if exposure_count + 1 > config.max_open_positions:
        return ("최대 동시 포지션 수를 초과합니다",)

    candidate_risk = Decimal(str(candidate.planned_risk))
    candidate_notional = Decimal(str(candidate.notional))
    quantity = Decimal(candidate.quantity)
    expected_risk = Decimal(str(candidate.risk_per_share)) * quantity
    expected_notional = Decimal(str(candidate.intent.entry_limit)) * quantity
    if (
        abs(candidate_risk - expected_risk) > MONEY_TOLERANCE
        or abs(candidate_notional - expected_notional) > MONEY_TOLERANCE
    ):
        return ("후보 주문의 수량·위험·명목금액 계산이 서로 일치하지 않습니다",)
    max_risk = Decimal(str(config.max_risk_dollars))
    max_notional = Decimal(str(config.max_notional_dollars))
    daily_loss_limit = Decimal(str(config.daily_loss_limit_dollars))
    conservative_equity = min(
        portfolio.equity,
        portfolio.last_equity,
        Decimal(str(config.reference_equity)),
    )
    effective_risk_limit = min(
        max_risk,
        conservative_equity * Decimal(str(config.risk_fraction)),
    )
    effective_notional_limit = min(
        max_notional,
        conservative_equity * MAX_SYMBOL_NOTIONAL_FRACTION,
    )
    if any(
        exposure.planned_risk > effective_risk_limit
        or exposure.gross_exposure > effective_notional_limit
        for exposure in portfolio.exposures
    ):
        return ("기존 종목 노출이 종목당 위험 또는 명목 한도를 초과합니다",)
    if (
        candidate_risk > effective_risk_limit
        or candidate_notional > effective_notional_limit
    ):
        return ("후보 주문이 종목당 위험 또는 명목 한도를 초과합니다",)
    if portfolio.equity - portfolio.last_equity <= -daily_loss_limit:
        return ("당일 손실 중단선에 도달했습니다",)
    resulting_slot_count = exposure_count + 1
    if portfolio.planned_open_risk + candidate_risk > (
        effective_risk_limit * resulting_slot_count
    ):
        return ("기존 예약분을 포함한 총 계획위험 한도를 초과합니다",)
    gross_limit = min(
        conservative_equity,
        effective_notional_limit * resulting_slot_count,
    )
    if portfolio.gross_exposure + candidate_notional > gross_limit:
        return ("기존 예약분을 포함한 총 익스포저 한도를 초과합니다",)
    if portfolio.buying_power < candidate_notional:
        return ("현재 buying power가 후보 주문 명목금액보다 작습니다",)
    return ()


def _is_current(now: dt.datetime, observed_at: dt.datetime) -> bool:
    if not _is_aware(now) or not _is_aware(observed_at):
        return False
    age = now.astimezone(dt.UTC) - observed_at.astimezone(dt.UTC)
    return dt.timedelta(0) <= age <= CURRENT_PORTFOLIO_AGE


def _order_geometry_is_valid(candidate: SizedPaperOrder) -> bool:
    entry = Decimal(str(candidate.intent.entry_limit))
    stop = Decimal(str(candidate.intent.stop))
    target_1r = Decimal(str(candidate.intent.target_1r))
    target_2r = Decimal(str(candidate.intent.target_2r))
    risk_per_share = Decimal(str(candidate.risk_per_share))
    if min(entry, stop, target_1r, target_2r, risk_per_share) <= 0:
        return False
    match candidate.intent.side:
        case PaperOrderSide.BUY:
            stop_distance = entry - stop
            targets_valid = entry < target_1r < target_2r
        case PaperOrderSide.SELL:
            stop_distance = stop - entry
            targets_valid = target_2r < target_1r < entry
        case unreachable:
            assert_never(unreachable)
    return stop_distance > 0 and risk_per_share >= stop_distance and targets_valid


def _is_aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
