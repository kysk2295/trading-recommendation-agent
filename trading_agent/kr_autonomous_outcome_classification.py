from __future__ import annotations

import datetime as dt
from typing import Final, assert_never
from zoneinfo import ZoneInfo

from trading_agent.kr_autonomous_outcome_models import (
    KrOutcomeMarketEvidenceState,
    KrOutcomeSessionPhase,
)
from trading_agent.kr_autonomous_trade_models import (
    KrAutonomousNoTrade,
    KrAutonomousRejected,
    KrAutonomousTradeEvent,
    KrNoTradeReason,
    KrTradeRecommendation,
)

_SEOUL: Final = ZoneInfo("Asia/Seoul")


def outcome_market_state(event: KrAutonomousTradeEvent) -> KrOutcomeMarketEvidenceState:
    match event:
        case KrTradeRecommendation():
            return KrOutcomeMarketEvidenceState.CURRENT
        case KrAutonomousNoTrade():
            reasons = set(event.reason_codes)
            if KrNoTradeReason.STALE_MARKET in reasons:
                return KrOutcomeMarketEvidenceState.STALE
            if KrNoTradeReason.MISSING_SPREAD in reasons:
                return KrOutcomeMarketEvidenceState.MISSING_SPREAD
            return KrOutcomeMarketEvidenceState.CURRENT
        case KrAutonomousRejected():
            return KrOutcomeMarketEvidenceState.NOT_RECORDED
        case unreachable:
            assert_never(unreachable)


def outcome_session_phase(timestamp: dt.datetime) -> KrOutcomeSessionPhase:
    local = timestamp.astimezone(_SEOUL).time()
    if dt.time(9) <= local < dt.time(10):
        return KrOutcomeSessionPhase.OPENING
    if dt.time(10) <= local < dt.time(14, 30):
        return KrOutcomeSessionPhase.CONTINUOUS
    if dt.time(14, 30) <= local <= dt.time(15, 30):
        return KrOutcomeSessionPhase.CLOSING
    return KrOutcomeSessionPhase.OUTSIDE_SESSION


__all__ = ("outcome_market_state", "outcome_session_phase")
