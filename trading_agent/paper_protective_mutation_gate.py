from __future__ import annotations

import datetime as dt
from typing import Final

from trading_agent.paper_order_gate_models import CompletePaperPortfolio
from trading_agent.paper_runtime import PaperRuntimeReadiness
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds

CURRENT_PROTECTIVE_SNAPSHOT_AGE: Final = dt.timedelta(seconds=5)
EOD_PROTECTIVE_CUTOFF_BEFORE_CLOSE: Final = dt.timedelta(minutes=5)


def protective_mutation_readiness_reasons(
    readiness: PaperRuntimeReadiness,
    evaluated_at: dt.datetime,
) -> tuple[str, ...]:
    reasons = [*readiness.runtime_reasons]
    if not readiness.reconciliation.ready:
        reasons.extend(readiness.reconciliation.reasons)
    if not isinstance(readiness.portfolio, CompletePaperPortfolio):
        reasons.extend(readiness.portfolio.reasons)

    state = readiness.broker_state
    market_clock = readiness.market_clock
    rest_receipts = (
        state.account.observed_at,
        market_clock.observed_at,
        market_clock.market_timestamp,
        *(snapshot.observed_at for snapshot in state.protective_ocos),
    )
    if not _all_current(evaluated_at, rest_receipts):
        reasons.append("보호주문 mutation REST snapshot이 현재 5초 구간의 응답이 아닙니다")

    heartbeat = readiness.stream_heartbeat
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
        or not heartbeat.authorized_at <= heartbeat.subscribed_at <= heartbeat.pong_at
    ):
        reasons.append("trade_updates heartbeat 인증·구독·Pong 상태가 현재가 아닙니다")

    if state.account.status.upper() != "ACTIVE" or state.account.trading_blocked:
        reasons.append("Alpaca Paper 계좌가 보호주문 mutation 가능한 ACTIVE 상태가 아닙니다")

    now_new_york = evaluated_at.astimezone(NEW_YORK) if _is_aware(evaluated_at) else None
    bounds = None if now_new_york is None else regular_session_bounds(now_new_york.date())
    clock_complete = _is_aware(market_clock.next_open) and _is_aware(market_clock.next_close)
    if (
        now_new_york is None
        or bounds is None
        or not clock_complete
        or not market_clock.is_open
        or not bounds[0] <= now_new_york < bounds[1]
        or market_clock.next_close.astimezone(NEW_YORK) != bounds[1]
    ):
        reasons.append("브로커와 로컬 거래소의 열린 정규장 경계가 일치하지 않습니다")
    elif now_new_york >= bounds[1] - EOD_PROTECTIVE_CUTOFF_BEFORE_CLOSE:
        reasons.append("EOD 평탄화 5분 전부터 보호주문 mutation을 차단합니다")

    return tuple(dict.fromkeys(reasons))


def _all_current(
    now: dt.datetime,
    observed_values: tuple[dt.datetime, ...],
) -> bool:
    return all(_is_current(now, observed_at) for observed_at in observed_values)


def _is_current(now: dt.datetime, observed_at: dt.datetime) -> bool:
    if not _is_aware(now) or not _is_aware(observed_at):
        return False
    age = now.astimezone(dt.UTC) - observed_at.astimezone(dt.UTC)
    return dt.timedelta(0) <= age <= CURRENT_PROTECTIVE_SNAPSHOT_AGE


def _is_aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
