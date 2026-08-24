from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from typing import assert_never, override

from trading_agent.models import RecommendationEvent, RecommendationState
from trading_agent.us_day_thesis_models import DayTradeDecision, UsDayTradeThesis


class UsDayLifecycleStatus(StrEnum):
    INVESTIGATING = "INVESTIGATING"
    ARMED = "ARMED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    STOPPED = "STOPPED"
    TARGETED = "TARGETED"
    CENSORED = "CENSORED"


class InvalidUsDayLifecycleError(ValueError):
    @override
    def __str__(self) -> str:
        return "US day lifecycle history is invalid"


@dataclass(frozen=True, slots=True)
class UsDayLifecycleEvent:
    thesis_id: str
    status: UsDayLifecycleStatus
    occurred_at: dt.datetime
    source_ref: str
    reason: str

    @property
    def transition_id(self) -> str:
        return f"{self.thesis_id}:{self.status.value}"


def derive_us_day_lifecycle(
    thesis: UsDayTradeThesis,
    paper_events: tuple[RecommendationEvent, ...],
) -> tuple[UsDayLifecycleEvent, ...]:
    try:
        checked = UsDayTradeThesis.model_validate(thesis.model_dump(mode="python"))
    except ValueError:
        raise InvalidUsDayLifecycleError from None
    initial = _initial_status(checked.decision)
    lifecycle = [
        UsDayLifecycleEvent(
            checked.thesis_id,
            initial,
            checked.observed_at,
            f"thesis:{checked.thesis_id}",
            checked.reason_code or checked.invalidation_rule,
        )
    ]
    prior_at = checked.observed_at
    for event in paper_events:
        if (
            event.recommendation_id != checked.thesis_id
            or event.occurred_at.tzinfo is None
            or event.occurred_at.utcoffset() is None
            or event.occurred_at < prior_at
        ):
            raise InvalidUsDayLifecycleError
        prior_at = event.occurred_at
        status = _paper_status(event.state)
        if status is not lifecycle[-1].status:
            lifecycle.append(
                UsDayLifecycleEvent(
                    checked.thesis_id,
                    status,
                    event.occurred_at,
                    _paper_event_ref(event),
                    event.note,
                )
            )
    if initial is not UsDayLifecycleStatus.ARMED and paper_events:
        raise InvalidUsDayLifecycleError
    if paper_events and paper_events[0].state is not RecommendationState.SETUP:
        raise InvalidUsDayLifecycleError
    if not _legal_statuses(tuple(item.status for item in lifecycle)):
        raise InvalidUsDayLifecycleError
    return tuple(lifecycle)


def _initial_status(decision: DayTradeDecision) -> UsDayLifecycleStatus:
    match decision:
        case DayTradeDecision.WATCH:
            return UsDayLifecycleStatus.INVESTIGATING
        case DayTradeDecision.RECOMMEND:
            return UsDayLifecycleStatus.ARMED
        case DayTradeDecision.NO_TRADE | DayTradeDecision.INSUFFICIENT_EVIDENCE:
            return UsDayLifecycleStatus.REJECTED
        case unreachable:
            assert_never(unreachable)


def _paper_status(state: RecommendationState) -> UsDayLifecycleStatus:
    match state:
        case RecommendationState.SETUP:
            return UsDayLifecycleStatus.ARMED
        case RecommendationState.ACTIVE | RecommendationState.TARGET_1R:
            return UsDayLifecycleStatus.ACTIVE
        case RecommendationState.INVALIDATED:
            return UsDayLifecycleStatus.REJECTED
        case RecommendationState.STOPPED:
            return UsDayLifecycleStatus.STOPPED
        case RecommendationState.TARGET_2R:
            return UsDayLifecycleStatus.TARGETED
        case RecommendationState.CAUSALITY_EXCLUDED | RecommendationState.TIME_EXIT:
            return UsDayLifecycleStatus.CENSORED
        case unreachable:
            assert_never(unreachable)


def _paper_event_ref(event: RecommendationEvent) -> str:
    material = json.dumps(
        (
            event.recommendation_id,
            event.occurred_at.isoformat(),
            event.state.value,
            event.price,
            event.note,
        ),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return f"paper-event:{hashlib.sha256(material.encode()).hexdigest()}"


def _legal_statuses(statuses: tuple[UsDayLifecycleStatus, ...]) -> bool:
    allowed = {
        UsDayLifecycleStatus.INVESTIGATING: frozenset(),
        UsDayLifecycleStatus.ARMED: frozenset(
            {
                UsDayLifecycleStatus.ACTIVE,
                UsDayLifecycleStatus.REJECTED,
                UsDayLifecycleStatus.CENSORED,
            }
        ),
        UsDayLifecycleStatus.ACTIVE: frozenset(
            {
                UsDayLifecycleStatus.STOPPED,
                UsDayLifecycleStatus.TARGETED,
                UsDayLifecycleStatus.CENSORED,
            }
        ),
        UsDayLifecycleStatus.REJECTED: frozenset(),
        UsDayLifecycleStatus.STOPPED: frozenset(),
        UsDayLifecycleStatus.TARGETED: frozenset(),
        UsDayLifecycleStatus.CENSORED: frozenset(),
    }
    return all(current in allowed[prior] for prior, current in pairwise(statuses))


__all__ = (
    "InvalidUsDayLifecycleError",
    "UsDayLifecycleEvent",
    "UsDayLifecycleStatus",
    "derive_us_day_lifecycle",
)
