from __future__ import annotations

from trading_agent.hermes_delivery_models import HermesDeliveryKind
from trading_agent.hermes_delivery_projection import HermesProjectionRecord
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_day_decision_delivery_identity import (
    same_kr_day_thesis,
)
from trading_agent.kr_day_decision_delivery_record_builders import (
    InvalidKrDayDecisionDeliveryError,
    active_record,
    armed_record,
    bound_kr_day_decision_id,
    censored_exit_record,
    decision_reply,
    exit_record,
    shadow_reply,
)
from trading_agent.kr_day_decision_models import KrDayDecisionEvent, KrDayDecisionStatus


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
            same_kr_day_thesis(prior, event) and prior.status is KrDayDecisionStatus.ARMED
            for prior in decisions[:index]
        )
    )
    records: list[HermesProjectionRecord] = []
    for root in roots:
        thesis_decisions = tuple(event for event in decisions if same_kr_day_thesis(event, root))
        armed_ids = frozenset(event.event_id for event in thesis_decisions if event.status is KrDayDecisionStatus.ARMED)
        history = tuple(event for event in shadows if bound_kr_day_decision_id(event) in armed_ids)
        records.extend(_thread_records(root, thesis_decisions, history))
    return tuple(records)


def _thread_records(
    root: KrDayDecisionEvent,
    decisions: tuple[KrDayDecisionEvent, ...],
    shadows: tuple[KrDayCapsuleShadowEvent, ...],
) -> tuple[HermesProjectionRecord, ...]:
    records = [armed_record(root)]
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
    if active is not None and (invalidation is not None or blocked is not None):
        raise InvalidKrDayDecisionDeliveryError
    if invalidation is not None:
        records.append(decision_reply(root, invalidation))
    elif blocked is not None:
        records.append(shadow_reply(root, blocked, HermesDeliveryKind.INVALIDATION, "조건부 추천 차단"))
    if failed is not None:
        records.append(shadow_reply(root, failed, HermesDeliveryKind.INCIDENT, "shadow 서비스 실패"))
    if active is not None:
        records.append(active_record(root, active))
        if terminal is not None:
            records.append(exit_record(root, active, terminal))
    elif terminal is not None:
        preceding = shadows[: shadows.index(terminal)]
        no_fill = (
            terminal.status is KrDayCapsuleShadowStatus.CENSORED
            and terminal.entry_price is None
            and terminal.stop_price is None
            and not terminal.target_prices
            and bool(preceding)
            and all(event.status is KrDayCapsuleShadowStatus.REGISTERED for event in preceding)
        )
        if not no_fill:
            raise InvalidKrDayDecisionDeliveryError
        records.append(censored_exit_record(root, preceding, terminal))
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


__all__ = ("InvalidKrDayDecisionDeliveryError", "bound_kr_day_decision_id", "build_kr_day_decision_records")
