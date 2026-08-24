from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Final, override

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import HermesProjectionRecord
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_day_decision_delivery_rendering import (
    render_active_shadow,
    render_armed_decision,
    render_shadow_exit,
)
from trading_agent.kr_day_decision_models import KrDayDecisionEvent, KrDayDecisionStatus
from trading_agent.kr_theme_lane import KR_THEME_LEADER_VWAP_RECLAIM_LANE

_SIGNAL_PREFIX: Final = "kr-day-decision-"


class InvalidKrDayDecisionDeliveryError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day decision delivery history is invalid"


def build_kr_day_decision_records(
    decisions: tuple[KrDayDecisionEvent, ...],
    shadows: tuple[KrDayCapsuleShadowEvent, ...],
) -> tuple[HermesProjectionRecord, ...]:
    by_id = {event.event_id: event for event in decisions}
    _require_bound_lineage(by_id, shadows)
    roots = tuple(
        event
        for index, event in enumerate(decisions)
        if event.status is KrDayDecisionStatus.ARMED
        and not any(
            _same_thesis(prior, event) and prior.status is KrDayDecisionStatus.ARMED
            for prior in decisions[:index]
        )
    )
    records: list[HermesProjectionRecord] = []
    for root in roots:
        thesis_decisions = tuple(event for event in decisions if _same_thesis(event, root))
        armed_ids = frozenset(
            event.event_id for event in thesis_decisions if event.status is KrDayDecisionStatus.ARMED
        )
        history = tuple(event for event in shadows if bound_kr_day_decision_id(event) in armed_ids)
        records.extend(_thread_records(root, thesis_decisions, history))
    return tuple(records)


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


def _thread_records(
    root: KrDayDecisionEvent,
    decisions: tuple[KrDayDecisionEvent, ...],
    shadows: tuple[KrDayCapsuleShadowEvent, ...],
) -> tuple[HermesProjectionRecord, ...]:
    records = [_armed_record(root)]
    invalidation = next(
        (
            event
            for event in decisions
            if event.status in {KrDayDecisionStatus.REJECTED, KrDayDecisionStatus.BLOCKED}
            and event.observed_at >= root.observed_at
        ),
        None,
    )
    active = next((event for event in shadows if event.status is KrDayCapsuleShadowStatus.ACTIVE), None)
    blocked = next((event for event in shadows if event.status is KrDayCapsuleShadowStatus.BLOCKED), None)
    failed = next((event for event in shadows if event.status is KrDayCapsuleShadowStatus.FAILED), None)
    terminal = next(
        (
            event
            for event in shadows
            if event.status
            in {
                KrDayCapsuleShadowStatus.STOPPED,
                KrDayCapsuleShadowStatus.TARGETED,
                KrDayCapsuleShadowStatus.CENSORED,
            }
        ),
        None,
    )
    if invalidation is not None:
        records.append(_decision_reply(root, invalidation))
    elif blocked is not None:
        records.append(_shadow_reply(root, blocked, HermesDeliveryKind.INVALIDATION, "조건부 추천 차단"))
    if failed is not None:
        records.append(_shadow_reply(root, failed, HermesDeliveryKind.INCIDENT, "shadow 서비스 실패"))
    if active is not None:
        records.append(_active_record(root, active))
        if terminal is not None:
            records.append(_exit_record(root, active, terminal))
    elif terminal is not None:
        raise InvalidKrDayDecisionDeliveryError
    return tuple(records)


def _require_bound_lineage(
    decisions: dict[str, KrDayDecisionEvent],
    shadows: tuple[KrDayCapsuleShadowEvent, ...],
) -> None:
    for event in shadows:
        decision_id = bound_kr_day_decision_id(event)
        if decision_id is None:
            continue
        decision = decisions.get(decision_id)
        if (
            decision is None
            or event.capsule_id != decision.capsule_id
            or event.session_date != decision.session_date
            or event.symbol != decision.symbol
            or (
                event.status is not KrDayCapsuleShadowStatus.REGISTERED
                and decision.status is not KrDayDecisionStatus.ARMED
            )
        ):
            raise InvalidKrDayDecisionDeliveryError


def _armed_record(decision: KrDayDecisionEvent) -> HermesProjectionRecord:
    return _record(
        decision,
        "armed",
        HermesDeliveryKind.ACTIONABLE,
        decision.observed_at,
        decision.status.value.lower(),
        tuple(sorted((*decision.evidence_refs, f"decision:{decision.event_id}"))),
        render_armed_decision(decision),
        decision,
    )


def _active_record(decision: KrDayDecisionEvent, event: KrDayCapsuleShadowEvent) -> HermesProjectionRecord:
    return _shadow_record(
        decision,
        event,
        "active",
        HermesDeliveryKind.ACTIONABLE,
        render_active_shadow(event),
    )


def _exit_record(
    decision: KrDayDecisionEvent,
    active: KrDayCapsuleShadowEvent,
    terminal: KrDayCapsuleShadowEvent,
) -> HermesProjectionRecord:
    return _shadow_record(
        decision,
        terminal,
        f"exit:{terminal.status.value}",
        HermesDeliveryKind.EXIT,
        render_shadow_exit(terminal),
        (f"shadow:{active.event_id}",),
    )


def _decision_reply(root: KrDayDecisionEvent, event: KrDayDecisionEvent) -> HermesProjectionRecord:
    return _record(
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


def _shadow_reply(
    root: KrDayDecisionEvent,
    event: KrDayCapsuleShadowEvent,
    kind: HermesDeliveryKind,
    label: str,
) -> HermesProjectionRecord:
    return _shadow_record(
        root,
        event,
        f"{kind.value}:{event.status.value}",
        kind,
        f"KR Day {label}\n- 영향 종목: {event.symbol}\n- 정확한 사유: {event.reason.value}",
    )


def _shadow_record(
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
    return _record(
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


def _record(
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
        source_event_id=_source_id(decision, state),
        root_source_event_id=_source_id(decision, "armed") if reply else None,
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


def _source_id(decision: KrDayDecisionEvent, state: str) -> str:
    material = (
        decision.capsule_id,
        decision.hypothesis_version_id,
        decision.opportunity_id,
        decision.session_date.isoformat(),
        decision.symbol,
        state,
    )
    digest = hashlib.sha256(json.dumps(material, separators=(",", ":")).encode()).hexdigest()
    return f"kr-day:{state.split(':')[0]}:{digest}"


def _same_thesis(left: KrDayDecisionEvent, right: KrDayDecisionEvent) -> bool:
    return _thesis(left) == _thesis(right)


def _thesis(event: KrDayDecisionEvent) -> tuple[str, str, str, dt.date, str]:
    return (event.capsule_id, event.hypothesis_version_id, event.opportunity_id, event.session_date, event.symbol)


__all__ = ("InvalidKrDayDecisionDeliveryError", "bound_kr_day_decision_id", "build_kr_day_decision_records")
