from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import assert_never, override

from trading_agent.experiment_ledger_models import (
    StrategyLifecycleEventKind,
    StrategyLifecycleState,
)
from trading_agent.experiment_ledger_store import StoredMultiMarketLifecycleEvent
from trading_agent.kr_theme_day_lifecycle_models import (
    KrThemeDayLifecycleDecision,
    KrThemeDayLifecycleOutcome,
    KrThemeDayLifecycleResult,
    decide_kr_theme_day_lifecycle,
)
from trading_agent.kr_theme_day_review_models import KrThemeDayReviewEvent
from trading_agent.multi_market_experiment_keys import (
    multi_market_hypothesis_registration_key,
    multi_market_strategy_version_registration_key,
)
from trading_agent.multi_market_experiment_store import (
    StoredMultiMarketHypothesisRegistration,
    StoredMultiMarketStrategyVersionRegistration,
)
from trading_agent.multi_market_lifecycle_keys import multi_market_lifecycle_event_key
from trading_agent.multi_market_lifecycle_models import MultiMarketStrategyLifecycleEvent


class InvalidKrThemeDayLifecycleProjectionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day lifecycle projection is not an exact replay"


@dataclass(frozen=True, slots=True)
class KrThemeDayLifecycleProjectionSource:
    hypothesis: StoredMultiMarketHypothesisRegistration
    version: StoredMultiMarketStrategyVersionRegistration
    review: KrThemeDayReviewEvent
    review_key: str
    next_session: dt.date
    events: tuple[StoredMultiMarketLifecycleEvent, ...]


def kr_theme_day_lifecycle_registration_event(
    source: KrThemeDayLifecycleProjectionSource,
    decided_at: dt.datetime,
    calendar_id: str,
    policy_version: str,
) -> MultiMarketStrategyLifecycleEvent:
    version = source.version.registration
    return MultiMarketStrategyLifecycleEvent(
        strategy_version=version.strategy_version,
        strategy_lane=version.strategy_lane,
        sequence=1,
        event_kind=StrategyLifecycleEventKind.REGISTRATION,
        from_state=None,
        to_state=StrategyLifecycleState.EXPERIMENTAL_SHADOW,
        policy_version=policy_version,
        decision_session_date=source.review.as_of_session,
        effective_session_date=source.next_session,
        decided_at=decided_at,
        session_calendar_snapshot_id=calendar_id,
        evidence_keys=tuple(
            sorted(
                (
                    calendar_id,
                    source.hypothesis.registration.experiment_scope_key,
                    str(multi_market_hypothesis_registration_key(source.hypothesis.registration)),
                    str(multi_market_strategy_version_registration_key(version)),
                )
            )
        ),
        reason_codes=("multi_market_strategy_registered",),
        previous_event_key=None,
    )


def kr_theme_day_lifecycle_transition_event(
    source: KrThemeDayLifecycleProjectionSource,
    current: StoredMultiMarketLifecycleEvent,
    decision: KrThemeDayLifecycleDecision,
    decided_at: dt.datetime,
    calendar_id: str,
    policy_version: str,
) -> MultiMarketStrategyLifecycleEvent:
    if decision.target_state is None:
        raise InvalidKrThemeDayLifecycleProjectionError
    previous_key = str(current.event_key)
    return MultiMarketStrategyLifecycleEvent(
        strategy_version=current.event.strategy_version,
        strategy_lane=current.event.strategy_lane,
        sequence=current.event.sequence + 1,
        event_kind=StrategyLifecycleEventKind.TRANSITION,
        from_state=current.event.to_state,
        to_state=decision.target_state,
        policy_version=policy_version,
        decision_session_date=source.review.as_of_session,
        effective_session_date=source.next_session,
        decided_at=decided_at,
        session_calendar_snapshot_id=calendar_id,
        evidence_keys=tuple(sorted((calendar_id, previous_key, source.review_key))),
        reason_codes=decision.reason_codes,
        previous_event_key=previous_key,
    )


def exact_kr_theme_day_lifecycle_replay(
    source: KrThemeDayLifecycleProjectionSource,
    *,
    as_of_session: dt.date,
    calendar_id: str,
    policy_version: str,
) -> KrThemeDayLifecycleResult | None:
    matches = tuple(
        (index, stored)
        for index, stored in enumerate(source.events)
        if stored.event.policy_version == policy_version and stored.event.decision_session_date == as_of_session
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise InvalidKrThemeDayLifecycleProjectionError
    index, stored = matches[0]
    event = stored.event
    match event.event_kind:
        case StrategyLifecycleEventKind.REGISTRATION:
            expected = kr_theme_day_lifecycle_registration_event(
                source,
                event.decided_at,
                calendar_id,
                policy_version,
            )
            outcome = KrThemeDayLifecycleOutcome.REGISTERED
            previous_state = None
            blockers: tuple[str, ...] = ()
        case StrategyLifecycleEventKind.TRANSITION:
            if index == 0:
                raise InvalidKrThemeDayLifecycleProjectionError
            previous = source.events[index - 1]
            decision = decide_kr_theme_day_lifecycle(previous.event.to_state, source.review.action)
            expected = kr_theme_day_lifecycle_transition_event(
                source,
                previous,
                decision,
                event.decided_at,
                calendar_id,
                policy_version,
            )
            outcome = KrThemeDayLifecycleOutcome.TRANSITIONED
            previous_state = previous.event.to_state
            blockers = decision.blockers
        case unreachable:
            assert_never(unreachable)
    if event != expected or stored.event_key != multi_market_lifecycle_event_key(expected):
        raise InvalidKrThemeDayLifecycleProjectionError
    return KrThemeDayLifecycleResult(
        outcome,
        False,
        previous_state,
        event.to_state,
        event.reason_codes,
        blockers,
        event,
    )
