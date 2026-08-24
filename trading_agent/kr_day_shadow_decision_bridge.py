from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final, override

from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluation
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowReason,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_day_decision_models import (
    KrDayDecisionEvent,
    KrDayDecisionReasonCode,
    KrDayDecisionStatus,
)
from trading_agent.kr_day_decision_store import KrDayDecisionStore
from trading_agent.kr_intraday_market_gate import (
    KrIntradayGateReason,
    assess_kr_shadow_entry,
)
from trading_agent.kr_theme_day_signal import kr_theme_day_spread_bps

_DECISION_SIGNAL_PREFIX: Final = "kr-day-decision-"


class InvalidKrDayShadowDecisionBridgeError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day shadow decision binding is invalid"


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
    return _assess_decision(evaluation, exact[0], gate.reasons)


def replay_kr_day_shadow_admission(
    evaluation: KrDayCapsuleEvaluation,
    event: KrDayCapsuleShadowEvent,
    store: KrDayDecisionStore,
) -> KrDayShadowAdmission | None:
    admission_event = event.status is KrDayCapsuleShadowStatus.REGISTERED or (
        event.status is KrDayCapsuleShadowStatus.ACTIVE
        and event.reason is KrDayCapsuleShadowReason.ENTRY
    )
    if not admission_event:
        return None
    event_id = _bound_decision_id(event.signal_id)
    gate = assess_kr_shadow_entry(evaluation.market, evaluation.evaluated_at)
    if event_id is None:
        if event.reason is KrDayCapsuleShadowReason.DECISION_MISSING:
            return _blocked(event.reason, None, (), gate.reasons)
        return None
    decision = store.event(event_id)
    if decision is None:
        raise InvalidKrDayShadowDecisionBridgeError
    if not _exact_lineage(evaluation, decision):
        if event.reason is KrDayCapsuleShadowReason.DECISION_MISMATCH:
            return _blocked(event.reason, decision.event_id, decision.reason_codes, gate.reasons)
        raise InvalidKrDayShadowDecisionBridgeError
    return _assess_decision(evaluation, decision, gate.reasons)


def kr_day_decision_signal_id(event_id: str | None) -> str | None:
    return None if event_id is None else f"{_DECISION_SIGNAL_PREFIX}{event_id}"


def _bound_decision_id(signal_id: str | None) -> str | None:
    if signal_id is None or not signal_id.startswith(_DECISION_SIGNAL_PREFIX):
        return None
    event_id = signal_id.removeprefix(_DECISION_SIGNAL_PREFIX)
    if len(event_id) != 64 or any(character not in "0123456789abcdef" for character in event_id):
        raise InvalidKrDayShadowDecisionBridgeError
    return event_id


def _assess_decision(
    evaluation: KrDayCapsuleEvaluation,
    decision: KrDayDecisionEvent,
    gate_reasons: tuple[KrIntradayGateReason, ...],
) -> KrDayShadowAdmission:
    if decision.status is not KrDayDecisionStatus.ARMED:
        return _blocked(
            KrDayCapsuleShadowReason.DECISION_NOT_ARMED,
            decision.event_id,
            decision.reason_codes,
            gate_reasons,
        )
    if decision.reason_codes == (KrDayDecisionReasonCode.CONDITIONAL_TRIGGER_PENDING,):
        return _blocked(
            KrDayCapsuleShadowReason.CONDITIONAL_TRIGGER_PENDING,
            decision.event_id,
            decision.reason_codes,
            gate_reasons,
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
            gate_reasons,
        )
    gate_blocked = bool(gate_reasons)
    if gate_blocked:
        return _blocked(
            KrDayCapsuleShadowReason.MARKET_GATE_BLOCKED,
            decision.event_id,
            decision.reason_codes,
            gate_reasons,
        )
    spread = kr_theme_day_spread_bps(evaluation.market)
    if spread is None or spread > evaluation.setup_input.max_slippage_bps:
        return _blocked(
            KrDayCapsuleShadowReason.SPREAD_TOO_WIDE,
            decision.event_id,
            decision.reason_codes,
            gate_reasons,
        )
    ask = evaluation.market.ask_price
    if ask is None or ask < plan.trigger_price:
        return _blocked(
            KrDayCapsuleShadowReason.CONDITIONAL_TRIGGER_PENDING,
            decision.event_id,
            decision.reason_codes,
            gate_reasons,
        )
    return KrDayShadowAdmission(
        True,
        KrDayCapsuleShadowReason.ENTRY,
        decision.event_id,
        decision.reason_codes,
        gate_reasons,
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


__all__ = (
    "InvalidKrDayShadowDecisionBridgeError",
    "KrDayShadowAdmission",
    "assess_kr_day_shadow_admission",
    "kr_day_decision_signal_id",
    "replay_kr_day_shadow_admission",
)
