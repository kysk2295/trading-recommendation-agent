from __future__ import annotations

from decimal import Decimal
from typing import override

from pydantic import ValidationError

from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import (
    SignalActionability,
    TradeSignalEnvelope,
)


class InvalidKrThemeDayRecommendationCardError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day recommendation card input is invalid"


def render_kr_theme_day_recommendation_card(signal: TradeSignalEnvelope) -> str:
    """Korean research card for KR day shadow signals. Never implies order authority."""
    try:
        checked = TradeSignalEnvelope.model_validate(signal.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError, ValueError):
        raise InvalidKrThemeDayRecommendationCardError from None
    if (
        checked.strategy_lane.market_id is not MarketId.KR_EQUITIES
        or checked.strategy_lane != KR_THEME_LEADER_VWAP_RECLAIM_LANE
    ):
        raise InvalidKrThemeDayRecommendationCardError
    is_conditional = checked.actionability is SignalActionability.CONDITIONAL
    title = (
        "한국 주식 조건부 테마 데이 신호"
        if is_conditional
        else "한국 주식 현재 호가 검증 테마 데이 신호"
    )
    actionability = (
        "조건부 (현재 호가 미검증 · 현재 진입 가능 아님)"
        if is_conditional
        else "현재 호가 검증 (shadow 연구 전용 · 국내 주문 없음)"
    )
    current_entry = "아니오" if is_conditional else "예 (호가 검증 시점 · 주문 권한 없음)"
    targets = " / ".join(
        f"{target.label} {_price(target.price)}" for target in checked.targets
    )
    quote = checked.quote_validation
    quote_lines = (
        ()
        if quote is None
        else (
            f"- 호가 관측 시각: {quote.observed_at.isoformat()}",
            f"- 현재 bid/ask: {_price(quote.bid)} / {_price(quote.ask)}",
            f"- spread: {_price(quote.spread_bps)} bp",
            f"- 트리거 상태: {'도달' if quote.ask >= checked.entry_price else '대기'}",
        )
    )
    lines = (
        f"# {title}",
        "",
        "> 연구 및 shadow forward-validation 후보이며 확정수익이나 자동주문이 아닙니다.",
        "> 국내 계좌·주문·잔고 API를 사용하지 않습니다. 이 카드는 주문 권한이 아닙니다.",
        "",
        f"- 신호 ID: {checked.signal_id}",
        f"- 시장: {checked.strategy_lane.market_id.value}",
        f"- 에이전트: {checked.strategy_lane.agent_family.value}",
        f"- 전략: {checked.strategy_lane.canonical_id}",
        f"- 전략 버전: {checked.producer_strategy_version}",
        f"- 신호 관측 시각: {checked.observed_at.isoformat()}",
        f"- 유효 종료: {checked.valid_until.isoformat()}",
        f"- 종목: {checked.symbol}",
        f"- 실행 가능성: {actionability}",
        f"- 현재 진입 가능: {current_entry}",
        "- 주문 권한: 없음 (KR shadow-only)",
        "- Paper 경로: 없음",
        *quote_lines,
        f"- 조건부 진입: {checked.entry_type.value} {_price(checked.entry_price)}",
        f"- 손절: {_price(checked.stop_price)}",
        f"- 목표: {targets}",
        f"- 무효화: {checked.invalidation_rule}",
        "- 같은 봉 충돌: 손절 우선",
        f"- 근거: {checked.rationale}",
        f"- 기회 ID: {checked.opportunity_id or '없음'}",
        "",
    )
    return "\n".join(lines)


def _price(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.0001"))
    text = format(quantized.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text if text else "0"


__all__ = (
    "InvalidKrThemeDayRecommendationCardError",
    "render_kr_theme_day_recommendation_card",
)
