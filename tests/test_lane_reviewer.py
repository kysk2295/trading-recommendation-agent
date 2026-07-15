from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from tests.daily_research_fixtures import write_complete_session
from trading_agent.adaptive_evaluation_models import (
    AdaptiveAction,
    AdaptiveEvaluation,
)
from trading_agent.daily_research_ledger import build_daily_record, write_daily_record
from trading_agent.daily_research_models import DailyResearchRecord
from trading_agent.lane_contract_keys import (
    experiment_scope_key,
    lane_daily_snapshot_key,
    lane_manifest_key,
)
from trading_agent.lane_contract_models import LaneDailySnapshot
from trading_agent.lane_defaults import (
    INTRADAY_MANIFEST,
    current_intraday_experiment_scope,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryReader, LaneRegistryStore
from trading_agent.lane_review_models import LaneReviewerAction
from trading_agent.lane_review_store import LaneReviewConflictError, LaneReviewStore
from trading_agent.lane_reviewer import (
    InvalidLaneReviewError,
    review_intraday_lane_day,
)
from trading_agent.strategy_factory import StrategyMode

SESSION_DATE = dt.date(2026, 7, 14)
FINALIZED_AT = dt.datetime(2026, 7, 15, 0, 5, tzinfo=dt.UTC)
REVIEWED_AT = dt.datetime(2026, 7, 15, 1, 30, tzinfo=dt.UTC)
ORB_SCOPE = current_intraday_experiment_scope("H-MOM-ORB-001")
ORB_SCOPE_KEY = experiment_scope_key(ORB_SCOPE)


@dataclass(frozen=True, slots=True)
class _ReviewSources:
    registry: LaneRegistryStore
    reviews: LaneReviewStore
    session: Path
    snapshot: LaneDailySnapshot
    record: DailyResearchRecord


@pytest.mark.parametrize(
    ("adaptive_action", "reviewer_action"),
    (
        (AdaptiveAction.COLLECTING, LaneReviewerAction.CONTINUE_COLLECTION),
        (AdaptiveAction.SHADOW_CONTINUE, LaneReviewerAction.CONTINUE_COLLECTION),
        (AdaptiveAction.EARLY_STOP, LaneReviewerAction.STOP_RECOMMENDED),
        (AdaptiveAction.SUSPEND, LaneReviewerAction.STOP_RECOMMENDED),
        (AdaptiveAction.DIAGNOSE, LaneReviewerAction.DIAGNOSIS_REQUIRED),
        (AdaptiveAction.COMPARISON_READY, LaneReviewerAction.COMPARISON_READY),
        (
            AdaptiveAction.PROMOTION_REVIEW,
            LaneReviewerAction.PROMOTION_REVIEW_BLOCKED,
        ),
    ),
)
def test_reviewer_maps_adaptive_action_without_authority(
    tmp_path: Path,
    adaptive_action: AdaptiveAction,
    reviewer_action: LaneReviewerAction,
) -> None:
    sources = _sources(tmp_path, adaptive_action=adaptive_action)

    result = review_intraday_lane_day(
        LaneRegistryReader(sources.registry.path),
        sources.reviews,
        sources.session,
        SESSION_DATE,
        reviewed_at=REVIEWED_AT,
    )

    assert result.created is True
    assert result.event.reviewer_action is reviewer_action
    assert result.event.adaptive_action is adaptive_action
    assert result.event.snapshot_key == lane_daily_snapshot_key(sources.snapshot)
    assert result.event.experiment_scope_key == ORB_SCOPE_KEY
    assert result.event.daily_record_id == sources.record.record_id
    assert result.event.automatic_state_change_allowed is False
    assert result.event.order_authority_change_allowed is False
    assert "allocation_ineligible" in result.event.blockers
    assert "champion_missing" in result.event.blockers
    assert len(sources.reviews.events()) == 1


@pytest.mark.parametrize(
    "case",
    (
        "missing_snapshot",
        "duplicate_snapshot",
        "nonflat_snapshot",
        "daily_date",
        "daily_scope",
        "daily_strategy_version",
        "parent_ledger_missing",
        "adaptive_malformed",
        "adaptive_date",
        "adaptive_strategy_version",
        "adaptive_evaluator_version",
    ),
)
def test_reviewer_rejects_invalid_sources_without_event(
    tmp_path: Path,
    case: str,
) -> None:
    sources = _sources(tmp_path, snapshot=case != "missing_snapshot")
    registry: LaneRegistryReader = LaneRegistryReader(sources.registry.path)
    if case == "duplicate_snapshot":
        registry = _malformed_snapshot_registry(
            tmp_path / "duplicate-registry.sqlite3",
            (sources.snapshot, sources.snapshot),
        )
    elif case == "nonflat_snapshot":
        nonflat = {
            **sources.snapshot.model_dump(mode="json"),
            "open_order_count": 1,
        }
        registry = _raw_snapshot_registry(
            tmp_path / "nonflat-registry.sqlite3",
            (json.dumps(nonflat),),
        )
    elif case.startswith("daily_"):
        _rewrite_daily_record(sources, case)
    elif case == "parent_ledger_missing":
        (sources.session.parent / "daily_research_ledger.jsonl").unlink()
    elif case == "adaptive_malformed":
        _adaptive_path(sources.session).write_text("{malformed", encoding="utf-8")
    elif case.startswith("adaptive_"):
        _rewrite_adaptive(sources.session, case)

    with pytest.raises(InvalidLaneReviewError):
        _ = review_intraday_lane_day(
            registry,
            sources.reviews,
            sources.session,
            SESSION_DATE,
            reviewed_at=REVIEWED_AT,
        )

    assert sources.reviews.events() == ()


def test_promotion_review_is_blocked_and_exact_replay_reuses_timestamp(
    tmp_path: Path,
) -> None:
    sources = _sources(tmp_path, adaptive_action=AdaptiveAction.PROMOTION_REVIEW)

    first = review_intraday_lane_day(
        LaneRegistryReader(sources.registry.path),
        sources.reviews,
        sources.session,
        SESSION_DATE,
        reviewed_at=REVIEWED_AT,
    )
    replay = review_intraday_lane_day(
        LaneRegistryReader(sources.registry.path),
        sources.reviews,
        sources.session,
        SESSION_DATE,
        reviewed_at=REVIEWED_AT + dt.timedelta(minutes=5),
    )

    assert first.created is True
    assert replay.created is False
    assert replay.event == first.event
    assert replay.event.reviewed_at == REVIEWED_AT
    assert replay.event.reviewer_action is LaneReviewerAction.PROMOTION_REVIEW_BLOCKED
    assert replay.event.automatic_state_change_allowed is False
    assert replay.event.order_authority_change_allowed is False
    assert {"allocation_ineligible", "champion_missing"} <= set(replay.event.blockers)
    assert len(sources.reviews.events()) == 1


def test_changed_adaptive_bytes_conflict_with_review_identity(tmp_path: Path) -> None:
    sources = _sources(tmp_path)
    _ = review_intraday_lane_day(
        LaneRegistryReader(sources.registry.path),
        sources.reviews,
        sources.session,
        SESSION_DATE,
        reviewed_at=REVIEWED_AT,
    )
    adaptive_path = _adaptive_path(sources.session)
    adaptive_path.write_text(
        adaptive_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(LaneReviewConflictError):
        _ = review_intraday_lane_day(
            LaneRegistryReader(sources.registry.path),
            sources.reviews,
            sources.session,
            SESSION_DATE,
            reviewed_at=REVIEWED_AT + dt.timedelta(minutes=5),
        )

    assert len(sources.reviews.events()) == 1


def _sources(
    tmp_path: Path,
    *,
    adaptive_action: AdaptiveAction = AdaptiveAction.COLLECTING,
    snapshot: bool = True,
) -> _ReviewSources:
    session = tmp_path / "live_sessions" / "20260714"
    write_complete_session(session, SESSION_DATE)
    record = build_daily_record(
        session,
        SESSION_DATE,
        StrategyMode.ORB,
        "test-code",
        FINALIZED_AT - dt.timedelta(minutes=3),
    )
    assert write_daily_record(session, record) is True

    daily_snapshot = _snapshot()
    registry = LaneRegistryStore(tmp_path / "lane-registry.sqlite3")
    with registry.writer() as writer:
        assert writer.register_manifest(INTRADAY_MANIFEST) is True
        assert writer.register_experiment_scope(ORB_SCOPE) is True
        if snapshot:
            assert writer.append_daily_snapshot(daily_snapshot) is True

    adaptive = AdaptiveEvaluation(
        schema_version=1,
        as_of=SESSION_DATE,
        strategy_version=record.strategy_version,
        evaluator_version=record.evaluator_version,
        action=adaptive_action,
        reasons=(f"{adaptive_action.value}_reason",),
        windows=(),
        regime_coverage=0.0,
        regimes=(),
        feature_coverage=0.0,
        gap_feature_coverage=0.0,
        cohorts=(),
        proof_blockers=("broker_paper_ledger_missing",),
        automatic_state_change_allowed=False,
    )
    path = _adaptive_path(session)
    path.parent.mkdir(parents=True)
    path.write_text(adaptive.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return _ReviewSources(
        registry,
        LaneReviewStore(tmp_path / "lane-review.sqlite3"),
        session,
        daily_snapshot,
        record,
    )


def _snapshot() -> LaneDailySnapshot:
    return LaneDailySnapshot(
        lane_id=LaneId.INTRADAY_MOMENTUM,
        session_date=SESSION_DATE,
        finalized_at=FINALIZED_AT,
        manifest_key=lane_manifest_key(INTRADAY_MANIFEST),
        experiment_scope_keys=(ORB_SCOPE_KEY,),
        source_ledger_generation=1,
        source_ledger_sha256="f" * 64,
        champion_strategy_versions=(),
        data_quality_complete=True,
        allocation_eligible=False,
        incidents=(),
        conservative_equity=Decimal("30000"),
        realized_pnl=Decimal(0),
        unrealized_pnl=Decimal(0),
        planned_open_risk=Decimal(0),
        open_order_count=0,
        open_position_count=0,
    )


def _rewrite_daily_record(sources: _ReviewSources, case: str) -> None:
    path = next((sources.session / "daily_research_records").glob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    if case == "daily_date":
        payload["session_date"] = "2026-07-15"
    elif case == "daily_scope":
        scope = current_intraday_experiment_scope("H-MOM-VWAP-001")
        payload.update(
            hypothesis_id=scope.hypothesis_id,
            experiment_scope=scope.model_dump(mode="json"),
            experiment_scope_key=experiment_scope_key(scope),
        )
    elif case == "daily_strategy_version":
        payload["strategy_version"] = "different_strategy_version"
    raw = json.dumps(payload, sort_keys=True) + "\n"
    path.write_text(raw, encoding="utf-8")
    (sources.session.parent / "daily_research_ledger.jsonl").write_text(
        raw,
        encoding="utf-8",
    )


def _rewrite_adaptive(session: Path, case: str) -> None:
    path = _adaptive_path(session)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if case == "adaptive_date":
        payload["as_of"] = "2026-07-15"
    elif case == "adaptive_strategy_version":
        payload["strategy_version"] = "different_strategy_version"
    elif case == "adaptive_evaluator_version":
        payload["evaluator_version"] = "different_evaluator_version"
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def _malformed_snapshot_registry(
    path: Path,
    snapshots: tuple[LaneDailySnapshot, ...],
) -> LaneRegistryReader:
    return _raw_snapshot_registry(
        path,
        tuple(snapshot.model_dump_json() for snapshot in snapshots),
    )


def _raw_snapshot_registry(
    path: Path,
    payloads: tuple[str, ...],
) -> LaneRegistryReader:
    with sqlite3.connect(path) as connection:
        _ = connection.execute(
            """CREATE TABLE lane_daily_snapshots (
            snapshot_key TEXT, lane_id TEXT, session_date TEXT, payload_json TEXT)"""
        )
        for index, payload in enumerate(payloads):
            _ = connection.execute(
                "INSERT INTO lane_daily_snapshots VALUES (?, ?, ?, ?)",
                (
                    f"{index + 1:064x}",
                    LaneId.INTRADAY_MOMENTUM.value,
                    SESSION_DATE.isoformat(),
                    payload,
                ),
            )
        _ = connection.execute("PRAGMA user_version = 1")
    return LaneRegistryReader(path)


def _adaptive_path(session: Path) -> Path:
    return session / "adaptive_evaluation" / "adaptive_evaluation.json"
