from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluation
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowReason
from trading_agent.kr_day_decision_models import (
    KrDayDecisionEvent,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.kr_intraday_market_gate import (
    KrIntradayGateReason,
    KrIntradayGateStatus,
    assess_kr_shadow_entry,
)
from trading_agent.kr_theme_day_signal import kr_theme_day_spread_bps


@dataclass(frozen=True, slots=True)
class KrDayShadowAdmission:
    ready: bool
    reason: KrDayCapsuleShadowReason
    decision_event_id: str | None
    decision_reason_codes: tuple[KrDayDecisionReasonCode, ...]
    market_gate_reasons: tuple[KrIntradayGateReason, ...]
    trigger_price: Decimal | None = None
    stop_price: Decimal | None = None
    target_prices: tuple[Decimal, ...] = ()


def assess_kr_day_shadow_admission(
    evaluation: KrDayCapsuleEvaluation,
    store: KrDayDecisionStore,
) -> KrDayShadowAdmission:
    candidates = tuple(
        event
        for event in store.events()
        if event.capsule_id == evaluation.capsule_id
        and event.opportunity_id == evaluation.opportunity_id
        and event.session_date == evaluation.session_date
        and event.completed_bar_at == evaluation.completed_bar_cursor
    )
    exact = tuple(event for event in candidates if _exact_lineage(evaluation, event))
    gate = assess_kr_shadow_entry(evaluation.market, evaluation.evaluated_at)
    if not candidates:
        return _blocked(KrDayCapsuleShadowReason.DECISION_MISSING, None, (), gate.reasons)
    if len(exact) != 1:
        decision = candidates[-1]
        return _blocked(
            KrDayCapsuleShadowReason.DECISION_MISMATCH,
            decision.event_id,
            decision.reason_codes,
            gate.reasons,
        )
    decision = exact[0]
    if decision.status is not KrDayDecisionStatus.ARMED:
        return _blocked(
            KrDayCapsuleShadowReason.DECISION_NOT_ARMED,
            decision.event_id,
            decision.reason_codes,
            gate.reasons,
        )
    if decision.reason_codes == (KrDayDecisionReasonCode.CONDITIONAL_TRIGGER_PENDING,):
        return _blocked(
            KrDayCapsuleShadowReason.CONDITIONAL_TRIGGER_PENDING,
            decision.event_id,
            decision.reason_codes,
            gate.reasons,
        )
    plan = decision.conditional_plan
    if (
        plan is None
        or decision.reason_codes != (KrDayDecisionReasonCode.ENTRY_CONFIRMATION_READY,)
        or decision.valid_until <= evaluation.evaluated_at
    ):
        return _blocked(
            KrDayCapsuleShadowReason.DECISION_MISMATCH,
            decision.event_id,
            decision.reason_codes,
            gate.reasons,
        )
    if gate.status is KrIntradayGateStatus.BLOCKED:
        return _blocked(
            KrDayCapsuleShadowReason.MARKET_GATE_BLOCKED,
            decision.event_id,
            decision.reason_codes,
            gate.reasons,
        )
    spread = kr_theme_day_spread_bps(evaluation.market)
    if spread is None or spread > evaluation.setup_input.max_slippage_bps:
        return _blocked(
            KrDayCapsuleShadowReason.SPREAD_TOO_WIDE,
            decision.event_id,
            decision.reason_codes,
            gate.reasons,
        )
    ask = evaluation.market.ask_price
    if ask is None or ask < plan.trigger_price:
        return _blocked(
            KrDayCapsuleShadowReason.CONDITIONAL_TRIGGER_PENDING,
            decision.event_id,
            decision.reason_codes,
            gate.reasons,
        )
    return KrDayShadowAdmission(
        True,
        KrDayCapsuleShadowReason.ENTRY,
        decision.event_id,
        decision.reason_codes,
        gate.reasons,
        ask,
        plan.stop_price,
        plan.target_prices,
    )


def _exact_lineage(evaluation: KrDayCapsuleEvaluation, decision: KrDayDecisionEvent) -> bool:
    input_sha = next(
        (item.value for item in decision.observed_evidence if item.name == "decision_input_sha256"),
        None,
    )
    return (
        decision.capsule_id == evaluation.capsule_id
        and decision.hypothesis_version_id == evaluation.hypothesis_version_id
        and decision.opportunity_id == evaluation.opportunity_id
        and decision.symbol == evaluation.symbol
        and decision.completed_bar_at == evaluation.completed_bar_cursor
        and input_sha == evaluation.decision_input_sha256
    )


def _blocked(
    reason: KrDayCapsuleShadowReason,
    event_id: str | None,
    decision_reasons: tuple[KrDayDecisionReasonCode, ...],
    market_reasons: tuple[KrIntradayGateReason, ...],
) -> KrDayShadowAdmission:
    return KrDayShadowAdmission(False, reason, event_id, decision_reasons, market_reasons)


__all__ = ("KrDayShadowAdmission", "assess_kr_day_shadow_admission")
