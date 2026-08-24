from __future__ import annotations

from trading_agent.dashboard_projection_day_agent_support import InvalidKrDayLifecycleProjectionError
from trading_agent.kr_day_capsule_shadow_models import KrDayCapsuleShadowEvent, KrDayCapsuleShadowStatus
from trading_agent.kr_day_decision_delivery_record_builders import bound_kr_day_decision_id
from trading_agent.kr_day_decision_models import KrDayDecisionEvent, KrDayDecisionStatus


def has_bound_history(
    decisions: tuple[KrDayDecisionEvent, ...], shadows: tuple[KrDayCapsuleShadowEvent, ...]
) -> bool:
    try:
        _require_bound_history(decisions, shadows)
    except InvalidKrDayLifecycleProjectionError:
        return False
    return True


def _require_bound_history(
    decisions: tuple[KrDayDecisionEvent, ...], shadows: tuple[KrDayCapsuleShadowEvent, ...]
) -> None:
    by_id = {event.event_id: event for event in decisions}
    previous: str | None = None
    for shadow in shadows:
        if shadow.previous_event_id != previous:
            raise InvalidKrDayLifecycleProjectionError
        previous = shadow.event_id
        decision_id = bound_kr_day_decision_id(shadow)
        if decision_id is None:
            continue
        decision = by_id.get(decision_id)
        if decision is None or not _same_shadow_identity(decision, shadow):
            raise InvalidKrDayLifecycleProjectionError
        if (
            shadow.status is not KrDayCapsuleShadowStatus.REGISTERED
            and decision.status is not KrDayDecisionStatus.ARMED
        ):
            raise InvalidKrDayLifecycleProjectionError
        later = decisions[decisions.index(decision) + 1 :]
        if shadow.status is not KrDayCapsuleShadowStatus.REGISTERED and any(
            event.status is not KrDayDecisionStatus.ARMED for event in later
        ):
            raise InvalidKrDayLifecycleProjectionError


def _same_shadow_identity(decision: KrDayDecisionEvent, shadow: KrDayCapsuleShadowEvent) -> bool:
    return (
        decision.capsule_id == shadow.capsule_id
        and decision.session_date == shadow.session_date
        and decision.symbol == shadow.symbol
    )


__all__ = ("has_bound_history",)
