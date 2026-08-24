from __future__ import annotations

import datetime as dt

from tests.test_us_day_signal_admission import _eligible_request
from trading_agent.models import RecommendationEvent, RecommendationState
from trading_agent.us_day_lifecycle import (
    UsDayLifecycleStatus,
    derive_us_day_lifecycle,
)
from trading_agent.us_day_thesis_models import DayTradeDecision, UsDayTradeThesis


def test_derives_user_lifecycle_from_append_only_thesis_and_paper_events() -> None:
    # Given: one armed thesis followed by the persisted paper operating history.
    thesis = _eligible_request().thesis
    events = (
        _event(thesis.thesis_id, RecommendationState.SETUP, 7),
        _event(thesis.thesis_id, RecommendationState.ACTIVE, 8),
        _event(thesis.thesis_id, RecommendationState.ACTIVE, 9),
        _event(thesis.thesis_id, RecommendationState.TIME_EXIT, 10),
    )

    # When: the canonical lifecycle is derived.
    lifecycle = derive_us_day_lifecycle(thesis, events)

    # Then: repeated operating facts do not fabricate repeated user states.
    assert tuple(item.status for item in lifecycle) == (
        UsDayLifecycleStatus.ARMED,
        UsDayLifecycleStatus.ACTIVE,
        UsDayLifecycleStatus.CENSORED,
    )
    assert lifecycle[0].transition_id == f"{thesis.thesis_id}:ARMED"


def test_non_entry_theses_project_investigating_and_rejected() -> None:
    # Given: the existing append-only thesis decisions.
    recommendation = _eligible_request().thesis
    investigating = _terminal_thesis(
        recommendation,
        decision=DayTradeDecision.WATCH,
        reason_code="price_setup_incomplete",
    )
    rejected = _terminal_thesis(
        recommendation,
        decision=DayTradeDecision.NO_TRADE,
        reason_code="spread_too_wide",
    )

    # When / Then: user semantics are explicit even without an entry projection.
    assert derive_us_day_lifecycle(investigating, ())[0].status is UsDayLifecycleStatus.INVESTIGATING
    assert derive_us_day_lifecycle(rejected, ())[0].status is UsDayLifecycleStatus.REJECTED


def _terminal_thesis(
    source: UsDayTradeThesis,
    *,
    decision: DayTradeDecision,
    reason_code: str,
) -> UsDayTradeThesis:
    payload = source.model_dump(mode="python", exclude={"thesis_id"})
    payload.update(
        {
            "decision": decision,
            "symbol": None,
            "entry_price": None,
            "stop_price": None,
            "targets": (),
            "reason_code": reason_code,
            "theme_rationale": None,
            "catalyst_rationale": None,
            "leader_rationale": None,
            "flow_rationale": None,
            "evidence_refs": (),
        }
    )
    return UsDayTradeThesis.create(**payload)


def _event(thesis_id: str, state: RecommendationState, minute: int) -> RecommendationEvent:
    return RecommendationEvent(
        recommendation_id=thesis_id,
        occurred_at=dt.datetime(2026, 8, 20, 14, minute, tzinfo=dt.UTC),
        state=state,
        price=None,
        note=state.value,
    )
