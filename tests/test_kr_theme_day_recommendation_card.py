from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from trading_agent.kr_theme_day_recommendation_card import (
    InvalidKrThemeDayRecommendationCardError,
    render_kr_theme_day_recommendation_card,
)
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    QuoteValidation,
    SignalActionability,
    SignalEntryType,
    SignalSide,
    TradeSignalEnvelope,
    TradeTarget,
)

OBSERVED = dt.datetime(2026, 7, 20, 1, 5, 2, tzinfo=dt.UTC)


def test_kr_theme_day_card_is_shadow_only_korean_research_card() -> None:
    card = render_kr_theme_day_recommendation_card(_signal())

    assert card.startswith("# 한국 주식 현재 호가 검증 테마 데이 신호")
    assert "국내 계좌·주문·잔고 API를 사용하지 않습니다" in card
    assert "주문 권한: 없음 (KR shadow-only)" in card
    assert "Paper 경로: 없음" in card
    assert "시장: kr_equities" in card
    assert "전략: kr_equities/day_trading/theme_leader_vwap_reclaim" in card
    assert "종목: 005930" in card
    assert "조건부 진입: limit 10500" in card
    assert "손절: 10300" in card
    assert "미국 주식" not in card


def test_kr_card_rejects_us_or_wrong_lane_signals() -> None:
    us = _signal().model_copy(
        update={
            "strategy_lane": StrategyLaneRef(
                market_id=MarketId.US_EQUITIES,
                agent_family=AgentFamily.DAY_TRADING,
                strategy_id="orb",
            )
        }
    )
    with pytest.raises(InvalidKrThemeDayRecommendationCardError):
        render_kr_theme_day_recommendation_card(us)


def _signal() -> TradeSignalEnvelope:
    return TradeSignalEnvelope(
        signal_id="kr-theme-day-signal-1",
        strategy_lane=KR_THEME_LEADER_VWAP_RECLAIM_LANE,
        producer_strategy_version="theme-leader-vwap-v1",
        symbol="005930",
        observed_at=OBSERVED,
        valid_until=OBSERVED + dt.timedelta(seconds=30),
        side=SignalSide.LONG,
        entry_type=SignalEntryType.LIMIT,
        entry_price=Decimal("10500"),
        stop_price=Decimal("10300"),
        targets=(
            TradeTarget(label="1r", price=Decimal("10700")),
            TradeTarget(label="2r", price=Decimal("10900")),
        ),
        actionability=SignalActionability.CURRENT_QUOTE_VALIDATED,
        invalidation_rule="진입 전 손절가 이하, VI/거래정지, 정규장 종료 시 무효",
        rationale="테마 대장주 VWAP 재탈환",
        evidence_refs=(
            EvidenceRef(
                namespace="kr/theme/setup",
                record_id="setup-1",
                observed_at=OBSERVED - dt.timedelta(seconds=2),
            ),
        ),
        quote_validation=QuoteValidation(
            bid=Decimal("10490"),
            ask=Decimal("10500"),
            observed_at=OBSERVED - dt.timedelta(seconds=1),
            valid_until=OBSERVED + dt.timedelta(seconds=4),
            spread_bps=Decimal("9.53"),
            max_slippage_bps=Decimal("25"),
        ),
        opportunity_id="kr-opp-1",
    )
