from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_agent import causality
from trading_agent.metrics import extract_paper_trades
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.store import PaperStore

NEW_YORK = ZoneInfo("America/New_York")


def test_backdated_fill_is_excluded_without_deleting_audit_events(
    tmp_path: Path,
) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    recommendation = _recommendation()
    store.save(recommendation)
    store.set_state(
        recommendation.recommendation_id,
        RecommendationState.ACTIVE,
        dt.datetime(2026, 7, 10, 9, 33, tzinfo=NEW_YORK),
        recommendation.entry,
        "조건부 진입가 도달",
    )
    store.set_state(
        recommendation.recommendation_id,
        RecommendationState.TARGET_2R,
        dt.datetime(2026, 7, 10, 9, 33, tzinfo=NEW_YORK),
        recommendation.target_2r,
        "2R 목표가 도달",
    )
    original_events = store.events(recommendation.recommendation_id)
    audited_at = dt.datetime(2026, 7, 10, 10, 0, tzinfo=NEW_YORK)

    excluded = causality.exclude_backdated_recommendations(store, audited_at)

    persisted = store.recommendations()[0]
    events = store.events(recommendation.recommendation_id)
    assert excluded == 1
    assert persisted.state.value == "causality_excluded"
    assert events[:-1] == original_events
    assert events[-1].occurred_at == audited_at
    assert "인과성" in events[-1].note
    assert extract_paper_trades((store,)) == ()


def test_first_fully_post_alert_bar_is_not_excluded(tmp_path: Path) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    recommendation = _recommendation()
    store.save(recommendation)
    store.set_state(
        recommendation.recommendation_id,
        RecommendationState.ACTIVE,
        dt.datetime(2026, 7, 10, 9, 34, tzinfo=NEW_YORK),
        recommendation.entry,
        "조건부 진입가 도달",
    )

    excluded = causality.exclude_backdated_recommendations(
        store,
        dt.datetime(2026, 7, 10, 10, 0, tzinfo=NEW_YORK),
    )

    assert excluded == 0
    assert store.recommendations()[0].state is RecommendationState.ACTIVE


def test_causality_exclusion_is_idempotent(tmp_path: Path) -> None:
    store = PaperStore(tmp_path / "paper.sqlite3")
    recommendation = _recommendation()
    store.save(recommendation)
    store.set_state(
        recommendation.recommendation_id,
        RecommendationState.ACTIVE,
        dt.datetime(2026, 7, 10, 9, 33, tzinfo=NEW_YORK),
        recommendation.entry,
        "조건부 진입가 도달",
    )
    audited_at = dt.datetime(2026, 7, 10, 10, 0, tzinfo=NEW_YORK)
    _ = causality.exclude_backdated_recommendations(store, audited_at)
    event_count = len(store.events(recommendation.recommendation_id))

    excluded = causality.exclude_backdated_recommendations(store, audited_at)

    assert excluded == 0
    assert len(store.events(recommendation.recommendation_id)) == event_count


def _recommendation() -> Recommendation:
    return Recommendation(
        recommendation_id="rec-1",
        symbol="AAA",
        strategy="opening_range_breakout",
        created_at=dt.datetime(2026, 7, 10, 9, 33, 30, tzinfo=NEW_YORK),
        entry=10.0,
        stop=9.5,
        target_1r=10.5,
        target_2r=11.0,
        state=RecommendationState.SETUP,
        rationale="test",
    )
