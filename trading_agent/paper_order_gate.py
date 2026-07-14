from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Final

from trading_agent.paper_order_gate_models import (
    ApprovedPaperOrderGateDecision,
    BlockedPaperOrderGateDecision,
    CompletePaperPortfolio,
    PaperOrderGateDecision,
    PaperOrderGateSnapshot,
    PaperOrderGateState,
)
from trading_agent.paper_portfolio_gate import portfolio_reasons
from trading_agent.paper_risk import (
    DEFAULT_PAPER_RISK_CONFIG,
    PaperRiskConfig,
    PaperSizingContext,
    size_paper_order,
)
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

CURRENT_DATA_AGE: Final = dt.timedelta(seconds=5)
BAR_DURATION: Final = dt.timedelta(minutes=1)
ENTRY_CUTOFF_BEFORE_CLOSE: Final = dt.timedelta(minutes=30)


def _evaluate_reconciled_paper_order_gate(
    snapshot: PaperOrderGateSnapshot,
    evaluated_at: dt.datetime,
    config: PaperRiskConfig = DEFAULT_PAPER_RISK_CONFIG,
) -> PaperOrderGateDecision:
    config.assert_within_hard_limits()
    reasons = _session_reasons(snapshot, evaluated_at)
    if reasons:
        return _blocked(PaperOrderGateState.SESSION_BLOCKED, reasons)
    reasons = _current_bar_reasons(snapshot, evaluated_at)
    if reasons:
        return _blocked(PaperOrderGateState.CURRENT_BAR_BLOCKED, reasons)
    reasons = _stream_reasons(snapshot, evaluated_at)
    if reasons:
        return _blocked(PaperOrderGateState.STREAM_BLOCKED, reasons)
    if not isinstance(snapshot.portfolio, CompletePaperPortfolio):
        return _blocked(
            PaperOrderGateState.PORTFOLIO_BLOCKED,
            ("전체 포트폴리오 위험 집계가 불완전합니다",),
        )
    portfolio = snapshot.portfolio
    conservative_equity = float(
        min(
            portfolio.equity,
            portfolio.last_equity,
            Decimal(str(config.reference_equity)),
        )
    )
    candidate = size_paper_order(
        snapshot.candidate_intent,
        PaperSizingContext(
            conservative_equity=conservative_equity,
            liquidity_allowed_quantity=snapshot.liquidity_allowed_quantity,
            estimated_spread_bps=snapshot.estimated_spread_bps,
        ),
        config,
    )
    if candidate is None:
        return _blocked(
            PaperOrderGateState.PORTFOLIO_BLOCKED,
            ("후보 주문을 하드 위험 한도 안에서 산정할 수 없습니다",),
        )
    reasons = portfolio_reasons(portfolio, candidate, evaluated_at, config)
    if reasons:
        return _blocked(PaperOrderGateState.PORTFOLIO_BLOCKED, reasons)
    return ApprovedPaperOrderGateDecision(candidate)


def _session_reasons(
    snapshot: PaperOrderGateSnapshot,
    now: dt.datetime,
) -> tuple[str, ...]:
    clock = snapshot.market_clock
    if not (
        _is_current(now, clock.observed_at)
        and _is_current(now, clock.market_timestamp)
        and _is_aware(clock.next_open)
        and _is_aware(clock.next_close)
    ):
        return ("Alpaca 시장시계가 현재 시점의 완전한 응답이 아닙니다",)
    if not clock.is_open:
        return ("Alpaca 시장시계가 정규장 개장을 확인하지 않았습니다",)
    now_new_york = now.astimezone(NEW_YORK)
    bounds = regular_session_bounds(now_new_york.date())
    if bounds is None:
        return ("로컬 거래소 달력상 정규장이 아닙니다",)
    session_open, session_close = bounds
    if not session_open <= now_new_york < session_close:
        return ("현재 시각이 정규장 범위 밖입니다",)
    if clock.next_close.astimezone(NEW_YORK) != session_close:
        return ("브로커와 로컬 거래소 폐장시각이 일치하지 않습니다",)
    if now_new_york >= session_close - ENTRY_CUTOFF_BEFORE_CLOSE:
        return ("폐장 30분 전부터 신규 진입을 차단합니다",)
    return ()


def _current_bar_reasons(
    snapshot: PaperOrderGateSnapshot,
    now: dt.datetime,
) -> tuple[str, ...]:
    bar = snapshot.latest_bar
    intent = snapshot.candidate_intent
    if not all(
        _is_aware(value)
        for value in (now, bar.started_at, bar.first_observed_at, intent.created_at)
    ):
        return ("봉과 추천 시각은 timezone-aware 값이어야 합니다",)
    expected_start = (
        now.astimezone(NEW_YORK).replace(second=0, microsecond=0) - BAR_DURATION
    )
    bar_end = expected_start + BAR_DURATION
    bounds = regular_session_bounds(expected_start.date())
    if (
        bounds is None
        or expected_start < bounds[0]
        or bar_end > bounds[1]
        or bar.symbol != intent.symbol
        or not intent.symbol
        or intent.symbol != intent.symbol.upper()
        or bar.started_at.astimezone(NEW_YORK) != expected_start
        or not bar_end <= bar.first_observed_at.astimezone(NEW_YORK)
        or bar.first_observed_at > now
        or not bar.first_observed_at <= intent.created_at <= now
    ):
        return ("추천이 방금 완성되어 관측된 정확한 현재 1분봉에서 생성되지 않았습니다",)
    return ()


def _stream_reasons(
    snapshot: PaperOrderGateSnapshot,
    evaluated_at: dt.datetime,
) -> tuple[str, ...]:
    heartbeat = snapshot.stream_heartbeat
    if (
        not heartbeat.connection_epoch
        or not _is_current(evaluated_at, heartbeat.pong_at)
        or not all(
            _is_aware(value)
            for value in (
                heartbeat.authorized_at,
                heartbeat.subscribed_at,
                heartbeat.pong_at,
            )
        )
        or not heartbeat.authorized_at
        <= heartbeat.subscribed_at
        <= heartbeat.pong_at
    ):
        return ("trade_updates 연결의 인증·구독·Pong 상태가 현재가 아닙니다",)
    return ()


def _is_current(now: dt.datetime, observed_at: dt.datetime) -> bool:
    if not _is_aware(now) or not _is_aware(observed_at):
        return False
    age = now.astimezone(dt.UTC) - observed_at.astimezone(dt.UTC)
    return dt.timedelta(0) <= age <= CURRENT_DATA_AGE


def _is_aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _blocked(
    state: PaperOrderGateState,
    reasons: tuple[str, ...],
) -> PaperOrderGateDecision:
    return BlockedPaperOrderGateDecision(state, reasons)
