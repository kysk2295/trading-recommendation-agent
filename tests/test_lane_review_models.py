from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest
from pydantic import ValidationError

from trading_agent.adaptive_evaluation_models import AdaptiveAction
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_review_keys import (
    canonical_lane_review_json,
    lane_review_event_key,
)
from trading_agent.lane_review_models import LaneReviewerAction, LaneReviewEvent

REVIEWED_AT = dt.datetime(2026, 7, 15, 1, 30, tzinfo=dt.UTC)


def test_review_event_is_frozen_canonical_and_authority_denied() -> None:
    event = _event()

    assert event.automatic_state_change_allowed is False
    assert event.order_authority_change_allowed is False
    assert event.reviewer_action is LaneReviewerAction.CONTINUE_COLLECTION
    assert len(lane_review_event_key(event)) == 64
    assert lane_review_event_key(event) == lane_review_event_key(_event())
    encoded = canonical_lane_review_json(event)
    assert encoded == json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    assert lane_review_event_key(event) == hashlib.sha256(encoded.encode()).hexdigest()
    with pytest.raises(ValidationError):
        _ = LaneReviewEvent.model_validate({**event.model_dump(), "unexpected": "forbidden"})
    with pytest.raises(ValidationError):
        event.__setattr__("reviewer_version", "rewritten")


def test_reviewer_actions_are_closed_recommendations() -> None:
    assert tuple(LaneReviewerAction) == (
        LaneReviewerAction.CONTINUE_COLLECTION,
        LaneReviewerAction.STOP_RECOMMENDED,
        LaneReviewerAction.DIAGNOSIS_REQUIRED,
        LaneReviewerAction.COMPARISON_READY,
        LaneReviewerAction.PROMOTION_REVIEW_BLOCKED,
    )


@pytest.mark.parametrize(
    "field",
    (
        "snapshot_key",
        "experiment_scope_key",
        "daily_record_id",
        "daily_record_sha256",
        "adaptive_evaluation_sha256",
    ),
)
@pytest.mark.parametrize("invalid", ("a" * 63, "A" * 64, "g" * 64))
def test_review_event_rejects_invalid_hashes(field: str, invalid: str) -> None:
    payload = _event().model_dump()
    payload[field] = invalid

    with pytest.raises(ValidationError):
        _ = LaneReviewEvent.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("reviewed_at", dt.datetime(2026, 7, 15, 1, 30)),
        ("reasons", ("z_reason", "a_reason")),
        ("reasons", ("same", "same")),
        ("reasons", (" leading",)),
        ("blockers", ("z_blocker", "a_blocker")),
        ("blockers", ("same", "same")),
        ("blockers", ("trailing ",)),
        ("reviewer_version", "reviewer version"),
    ),
)
def test_review_event_rejects_noncanonical_lineage(
    field: str,
    invalid: object,
) -> None:
    payload = _event().model_dump()
    payload[field] = invalid

    with pytest.raises(ValidationError):
        _ = LaneReviewEvent.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    ("automatic_state_change_allowed", "order_authority_change_allowed"),
)
def test_review_event_rejects_authority_escalation(field: str) -> None:
    payload = _event().model_dump()
    payload[field] = True

    with pytest.raises(ValidationError):
        _ = LaneReviewEvent.model_validate(payload)


def _event() -> LaneReviewEvent:
    return LaneReviewEvent(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=dt.date(2026, 7, 14),
        snapshot_key="a" * 64,
        experiment_scope_key="b" * 64,
        daily_record_id="c" * 64,
        daily_record_sha256="d" * 64,
        adaptive_evaluation_sha256="e" * 64,
        strategy_version="orb_5m_buffer5bp_volume1.5_v1",
        evaluator_version="paper_metrics_day_block_bootstrap_v2",
        reviewer_version="lane_reviewer_v1",
        adaptive_action=AdaptiveAction.COLLECTING,
        reviewer_action=LaneReviewerAction.CONTINUE_COLLECTION,
        reasons=("minimum_five_day_observation_pending",),
        blockers=("allocation_ineligible", "champion_missing"),
        reviewed_at=REVIEWED_AT,
        automatic_state_change_allowed=False,
        order_authority_change_allowed=False,
    )
