from __future__ import annotations

import datetime as dt
import sqlite3
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.adaptive_evaluation_models import AdaptiveAction
from trading_agent.lane_policy_models import LaneId
from trading_agent.lane_review_keys import lane_review_event_key
from trading_agent.lane_review_models import LaneReviewerAction, LaneReviewEvent
from trading_agent.lane_review_schema import LANE_REVIEW_SCHEMA_VERSION
from trading_agent.lane_review_store import (
    InactiveLaneReviewWriterError,
    LaneReviewConflictError,
    LaneReviewReader,
    LaneReviewStore,
    LaneReviewWriterLeaseUnavailableError,
)

REVIEWED_AT = dt.datetime(2026, 7, 15, 1, 30, tzinfo=dt.UTC)


def test_review_schema_is_append_only_and_mode_600(tmp_path: Path) -> None:
    store = LaneReviewStore(tmp_path / "lane-review.sqlite3")
    with store.writer() as writer:
        assert writer.append_event(_event()) is True

    with sqlite3.connect(store.path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()
        tables = frozenset(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        )
        triggers = frozenset(
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE lane_review_events SET reviewer_version = 'changed'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM lane_review_events")

    assert version == (LANE_REVIEW_SCHEMA_VERSION,)
    assert tables == {"lane_review_events"}
    assert triggers == {
        "lane_review_events_no_delete",
        "lane_review_events_no_update",
    }
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_review_writer_lease_is_nonblocking_and_writer_expires(
    tmp_path: Path,
) -> None:
    store = LaneReviewStore(tmp_path / "lane-review.sqlite3")
    with (
        store.writer() as writer,
        pytest.raises(LaneReviewWriterLeaseUnavailableError),
        LaneReviewStore(store.path).writer(),
    ):
        pass

    with pytest.raises(InactiveLaneReviewWriterError):
        _ = writer.append_event(_event())


def test_review_reader_is_query_only_and_exact_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    store = LaneReviewStore(tmp_path / "lane-review.sqlite3")
    event = _event()
    with store.writer() as writer:
        assert writer.append_event(event) is True
        assert writer.append_event(event) is False

    reader = LaneReviewReader(store.path)
    stored = reader.events()
    selected = reader.review_event(
        event.snapshot_key,
        event.experiment_scope_key,
        event.reviewer_version,
    )

    assert len(stored) == 1
    assert stored[0].event_key == lane_review_event_key(event)
    assert stored[0].event == event
    assert selected == stored[0]
    assert not hasattr(reader, "writer")
    with reader._reader_connection() as connection:
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM lane_review_events")


def test_review_store_rejects_changed_payload_for_immutable_identity(
    tmp_path: Path,
) -> None:
    store = LaneReviewStore(tmp_path / "lane-review.sqlite3")
    event = _event()
    rewritten = event.model_copy(
        update={
            "reviewed_at": event.reviewed_at + dt.timedelta(seconds=1),
            "reasons": ("changed_recommendation",),
        }
    )

    with store.writer() as writer:
        assert writer.append_event(event) is True
        with pytest.raises(LaneReviewConflictError):
            _ = writer.append_event(rewritten)

    assert tuple(item.event for item in store.events()) == (event,)


def test_review_store_revalidates_copied_event_before_persistence(
    tmp_path: Path,
) -> None:
    store = LaneReviewStore(tmp_path / "lane-review.sqlite3")
    escalated = _event().model_copy(update={"automatic_state_change_allowed": True})

    with store.writer() as writer, pytest.raises(ValidationError):
        _ = writer.append_event(escalated)

    assert store.events() == ()


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
