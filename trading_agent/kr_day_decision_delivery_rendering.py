from __future__ import annotations

from decimal import Decimal

from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowEvent
from trading_agent.kr_day_decision_models import KrDayDecisionEvent


class InvalidKrDayDecisionRenderingError(ValueError):
    pass


def render_armed_decision(decision: KrDayDecisionEvent) -> str:
    plan = decision.conditional_plan
    if plan is None:
        raise InvalidKrDayDecisionRenderingError
    return (
        "KR Day 조건부 추천 (shadow 전용, 주문 권한 없음)\n"
        f"- 종목/시각: {decision.symbol} / {decision.observed_at.isoformat()}\n"
        f"- 진입 trigger/rule: {decimal_text(plan.trigger_price)} / {plan.trigger_rule}\n"
        f"- stop: {decimal_text(plan.stop_price)}\n"
        f"- targets: {', '.join(decimal_text(value) for value in plan.target_prices)}\n"
        f"- 무효화: {plan.invalidation_rule}\n"
        f"- valid_until: {plan.valid_until.isoformat()}\n"
        f"- 근거: {plan.rationale}\n"
        f"- evidence: {', '.join(plan.evidence_refs)}"
    )


def render_active_shadow(event: KrDayCapsuleShadowEvent) -> str:
    return (
        "KR Day shadow 체결 (실계좌 주문 없음)\n"
        f"- 종목/체결시각: {event.symbol} / {event.occurred_at.isoformat()}\n"
        f"- shadow 체결가: {required_decimal_text(event.entry_price)}\n"
        f"- stop: {required_decimal_text(event.stop_price)}\n"
        f"- targets: {', '.join(decimal_text(value) for value in event.target_prices)}"
    )


def render_censored_shadow(event: KrDayCapsuleShadowEvent) -> str:
    return (
        "KR Day shadow 미체결 계획 종료 (실계좌 주문 없음)\n"
        f"- 종목/종료시각: {event.symbol} / {event.occurred_at.isoformat()}\n"
        f"- 결과: {event.status.value} / {event.reason.value}\n"
        "- shadow 포지션 생성 전 계획이 종료되었습니다."
    )


def render_shadow_exit(event: KrDayCapsuleShadowEvent) -> str:
    return (
        "KR Day shadow 종료 (실계좌 주문 없음)\n"
        f"- 종목/종료시각: {event.symbol} / {event.occurred_at.isoformat()}\n"
        f"- 결과: {event.status.value} / {event.reason.value}\n"
        f"- shadow 진입가/stop/targets: {required_decimal_text(event.entry_price)} / "
        f"{required_decimal_text(event.stop_price)} / "
        f"{', '.join(decimal_text(value) for value in event.target_prices)}"
    )


def required_decimal_text(value: Decimal | None) -> str:
    if value is None:
        raise InvalidKrDayDecisionRenderingError
    return decimal_text(value)


def decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


__all__ = (
    "render_active_shadow",
    "render_armed_decision",
    "render_censored_shadow",
    "render_shadow_exit",
)
