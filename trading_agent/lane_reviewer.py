from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final, assert_never, override

from trading_agent.adaptive_evaluation_models import AdaptiveAction, AdaptiveEvaluation
from trading_agent.daily_research_contract import (
    EVALUATOR_VERSION,
    strategy_version_identity,
)
from trading_agent.daily_research_record_source import load_daily_research_record_source
from trading_agent.lane_contract_keys import (
    experiment_scope_key,
    lane_daily_snapshot_key,
    lane_manifest_key,
)
from trading_agent.lane_defaults import (
    INTRADAY_MANIFEST,
    current_intraday_experiment_scope,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryReader
from trading_agent.lane_review_models import (
    CURRENT_LANE_REVIEWER_VERSION,
    LaneReviewerAction,
    LaneReviewEvent,
)
from trading_agent.lane_review_store import LaneReviewStore
from trading_agent.strategy_factory import StrategyMode

LANE_REVIEWER_VERSION: Final = CURRENT_LANE_REVIEWER_VERSION
ORB_SCOPE: Final = current_intraday_experiment_scope("H-MOM-ORB-001")
ORB_SCOPE_KEY: Final = experiment_scope_key(ORB_SCOPE)


class InvalidLaneReviewError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "lane Reviewer가 finalized snapshot과 exact 연구 계보를 확인하지 못했습니다"


@dataclass(frozen=True, slots=True)
class LaneReviewResult:
    created: bool
    event: LaneReviewEvent


def review_intraday_lane_day(
    registry: LaneRegistryReader,
    reviews: LaneReviewStore,
    session: Path,
    session_date: dt.date,
    *,
    reviewed_at: dt.datetime,
) -> LaneReviewResult:
    try:
        event = _build_intraday_review_event(
            registry,
            reviews,
            session,
            session_date,
            reviewed_at=reviewed_at,
        )
    except (OSError, RuntimeError, UnicodeError, ValueError, sqlite3.Error):
        raise InvalidLaneReviewError from None
    with reviews.writer() as writer:
        created = writer.append_event(event)
    return LaneReviewResult(created, event)


def _build_intraday_review_event(
    registry: LaneRegistryReader,
    reviews: LaneReviewStore,
    session: Path,
    session_date: dt.date,
    *,
    reviewed_at: dt.datetime,
) -> LaneReviewEvent:
    if not _aware(reviewed_at):
        raise InvalidLaneReviewError
    stored_snapshot = registry.daily_snapshot(
        LaneId.INTRADAY_MOMENTUM,
        session_date,
    )
    if stored_snapshot is None:
        raise InvalidLaneReviewError
    snapshot = stored_snapshot.snapshot
    snapshot_key = lane_daily_snapshot_key(snapshot)
    if (
        stored_snapshot.snapshot_key != snapshot_key
        or snapshot.lane_id is not LaneId.INTRADAY_MOMENTUM
        or snapshot.session_date != session_date
        or snapshot.manifest_key != lane_manifest_key(INTRADAY_MANIFEST)
        or snapshot.experiment_scope_keys != (ORB_SCOPE_KEY,)
        or snapshot.open_order_count != 0
        or snapshot.open_position_count != 0
        or snapshot.planned_open_risk != 0
        or snapshot.unrealized_pnl != 0
        or snapshot.finalized_at > reviewed_at
    ):
        raise InvalidLaneReviewError

    source = load_daily_research_record_source(
        session,
        session_date,
        StrategyMode.ORB,
        ORB_SCOPE_KEY,
    )
    record = source.record
    if (
        record.experiment_scope != ORB_SCOPE
        or record.strategy_version != strategy_version_identity(
            StrategyMode.ORB,
            record.code_version,
        )
        or record.evaluator_version != EVALUATOR_VERSION
        or not _aware(record.recorded_at)
        or record.recorded_at > reviewed_at
    ):
        raise InvalidLaneReviewError

    adaptive_raw = (session / "adaptive_evaluation" / "adaptive_evaluation.json").read_bytes()
    adaptive = AdaptiveEvaluation.model_validate_json(adaptive_raw)
    if (
        adaptive.as_of != session_date
        or adaptive.strategy_version != record.strategy_version
        or adaptive.evaluator_version != record.evaluator_version
    ):
        raise InvalidLaneReviewError

    existing = reviews.review_event(
        snapshot_key,
        ORB_SCOPE_KEY,
        LANE_REVIEWER_VERSION,
    )
    event_reviewed_at = reviewed_at if existing is None else existing.event.reviewed_at
    blockers = set(snapshot.incidents)
    blockers.update(record.promotion.blockers)
    blockers.update(adaptive.proof_blockers)
    if not snapshot.data_quality_complete:
        blockers.add("data_quality_incomplete")
    if not snapshot.champion_strategy_versions:
        blockers.add("champion_missing")
    if not snapshot.allocation_eligible:
        blockers.add("allocation_ineligible")
    if adaptive.action is AdaptiveAction.PROMOTION_REVIEW:
        blockers.add("automatic_promotion_forbidden")

    return LaneReviewEvent(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=session_date,
        snapshot_key=snapshot_key,
        experiment_scope_key=ORB_SCOPE_KEY,
        daily_record_id=record.record_id,
        daily_record_sha256=source.raw_sha256,
        adaptive_evaluation_sha256=hashlib.sha256(adaptive_raw).hexdigest(),
        strategy_version=record.strategy_version,
        evaluator_version=record.evaluator_version,
        reviewer_version=LANE_REVIEWER_VERSION,
        adaptive_action=adaptive.action,
        reviewer_action=_reviewer_action(adaptive.action),
        reasons=tuple(sorted(set(adaptive.reasons))),
        blockers=tuple(sorted(blockers)),
        reviewed_at=event_reviewed_at,
        automatic_state_change_allowed=False,
        order_authority_change_allowed=False,
    )


def _reviewer_action(action: AdaptiveAction) -> LaneReviewerAction:
    match action:
        case AdaptiveAction.COLLECTING | AdaptiveAction.SHADOW_CONTINUE:
            return LaneReviewerAction.CONTINUE_COLLECTION
        case AdaptiveAction.EARLY_STOP | AdaptiveAction.SUSPEND:
            return LaneReviewerAction.STOP_RECOMMENDED
        case AdaptiveAction.DIAGNOSE:
            return LaneReviewerAction.DIAGNOSIS_REQUIRED
        case AdaptiveAction.COMPARISON_READY:
            return LaneReviewerAction.COMPARISON_READY
        case AdaptiveAction.PROMOTION_REVIEW:
            return LaneReviewerAction.PROMOTION_REVIEW_BLOCKED
        case unreachable:
            assert_never(unreachable)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
