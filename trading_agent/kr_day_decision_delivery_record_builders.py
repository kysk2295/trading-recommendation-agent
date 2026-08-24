from __future__ import annotations

import datetime as dt
import hashlib
from typing import Final, override

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import HermesProjectionRecord
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowEvent
from trading_agent.kr_day_decision_delivery_identity import kr_day_delivery_source_id
from trading_agent.kr_day_decision_delivery_rendering import (
    render_active_shadow,
    render_armed_decision,
    render_censored_shadow,
    render_shadow_exit,
)
from trading_agent.kr_day_decision_models import KrDayDecisionEvent
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE

_SIGNAL_PREFIX: Final = "kr-day-decision-"


class InvalidKrDayDecisionDeliveryError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day decision delivery history is invalid"


def bound_kr_day_decision_id(event: KrDayCapsuleShadowEvent) -> str | None:
    signal = event.signal_id
    if signal is None:
        return None
    if not signal.startswith(_SIGNAL_PREFIX):
        return None
    event_id = signal.removeprefix(_SIGNAL_PREFIX)
    if len(event_id) != 64 or any(character not in "0123456789abcdef" for character in event_id):
        raise InvalidKrDayDecisionDeliveryError
    return event_id


def armed_record(decision: KrDayDecisionEvent) -> HermesProjectionRecord:
    return record(
        decision,
        "armed",
        HermesDeliveryKind.ACTIONABLE,
        decision.observed_at,
        decision.status.value.lower(),
        tuple(sorted((*decision.evidence_refs, f"decision:{decision.event_id}"))),
        render_armed_decision(decision),
        decision,
    )


def active_record(
    decision: KrDayDecisionEvent,
    event: KrDayCapsuleShadowEvent,
) -> HermesProjectionRecord:
    return shadow_record(
        decision,
        event,
        "active",
        HermesDeliveryKind.ACTIONABLE,
        render_active_shadow(event),
    )


def exit_record(
    decision: KrDayDecisionEvent,
    active: KrDayCapsuleShadowEvent,
    terminal: KrDayCapsuleShadowEvent,
) -> HermesProjectionRecord:
    return shadow_record(
        decision,
        terminal,
        f"exit:{terminal.status.value}",
        HermesDeliveryKind.EXIT,
        render_shadow_exit(terminal),
        (f"shadow:{active.event_id}",),
    )


def censored_exit_record(
    decision: KrDayDecisionEvent,
    preceding: tuple[KrDayCapsuleShadowEvent, ...],
    terminal: KrDayCapsuleShadowEvent,
) -> HermesProjectionRecord:
    evidence = tuple(f"shadow:{event.event_id}" for event in preceding)
    return shadow_record(
        decision,
        terminal,
        "exit:censored",
        HermesDeliveryKind.EXIT,
        render_censored_shadow(terminal),
        evidence,
    )


def decision_reply(
    root: KrDayDecisionEvent,
    event: KrDayDecisionEvent,
) -> HermesProjectionRecord:
    return record(
        root,
        f"invalidation:{event.status.value.lower()}",
        HermesDeliveryKind.INVALIDATION,
        event.observed_at,
        event.status.value.lower(),
        tuple(sorted((*event.evidence_refs, f"decision:{root.event_id}", f"decision:{event.event_id}"))),
        "KR Day 조건부 추천 무효화\n"
        f"- 종목: {event.symbol}\n"
        f"- 사유: {', '.join(item.value for item in event.reason_codes)}",
        event,
        reply=True,
    )


def shadow_reply(
    root: KrDayDecisionEvent,
    event: KrDayCapsuleShadowEvent,
    kind: HermesDeliveryKind,
    label: str,
) -> HermesProjectionRecord:
    return shadow_record(
        root,
        event,
        f"{kind.value}:{event.status.value}",
        kind,
        f"KR Day {label}\n- 영향 종목: {event.symbol}\n- 정확한 사유: {event.reason.value}",
    )


def shadow_record(
    decision: KrDayDecisionEvent,
    event: KrDayCapsuleShadowEvent,
    state: str,
    kind: HermesDeliveryKind,
    text: str,
    extra: tuple[str, ...] = (),
) -> HermesProjectionRecord:
    bound = bound_kr_day_decision_id(event)
    decision_evidence = (f"decision:{decision.event_id}",)
    if bound is not None and bound != decision.event_id:
        decision_evidence += (f"decision:{bound}",)
    return record(
        decision,
        state,
        kind,
        event.occurred_at,
        event.status.value,
        tuple(sorted((*decision_evidence, f"shadow:{event.event_id}", *extra))),
        text,
        event,
        reply=True,
    )


def record(
    decision: KrDayDecisionEvent,
    state: str,
    kind: HermesDeliveryKind,
    occurred_at: dt.datetime,
    status: str,
    evidence: tuple[str, ...],
    text: str,
    payload: KrDayDecisionEvent | KrDayCapsuleShadowEvent,
    *,
    reply: bool = False,
) -> HermesProjectionRecord:
    lane = KR_THEME_LEADER_VWAP_RECLAIM_LANE
    return HermesProjectionRecord(
        source_event_id=kr_day_delivery_source_id(decision, state),
        root_source_event_id=kr_day_delivery_source_id(decision, "armed") if reply else None,
        kind=kind,
        market_id=lane.market_id.value,
        agent_family=lane.agent_family.value,
        lane_id=lane.canonical_id,
        strategy_version=decision.capsule_id,
        instrument_id=decision.symbol,
        occurred_at=occurred_at,
        status=status,
        evidence_refs=evidence,
        rendered_text=text,
        payload_sha256=hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest(),
    )


__all__ = (
    "InvalidKrDayDecisionDeliveryError",
    "active_record",
    "armed_record",
    "bound_kr_day_decision_id",
    "censored_exit_record",
    "decision_reply",
    "exit_record",
    "record",
    "shadow_record",
    "shadow_reply",
)
