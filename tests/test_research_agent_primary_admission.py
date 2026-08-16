from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from tests.research_agent_primary_fixtures import (
    NOW,
    seed_day,
    seed_market_context,
    seed_opportunity,
    source_paths,
)
from trading_agent.models import Recommendation, RecommendationState
from trading_agent.research_agent_source_adapters_primary import (
    DaySourceAdapter,
    MarketContextSourceAdapter,
    OpportunitySourceAdapter,
)
from trading_agent.research_agent_source_common import InvalidResearchAgentSourceError
from trading_agent.store import PaperStore


def test_current_primary_sources_are_admitted_with_bound_provenance(tmp_path: Path) -> None:
    # Given
    paths = source_paths(tmp_path)
    seed_opportunity(paths)
    seed_market_context(paths)
    seed_day(paths)

    # When
    evidence = (
        OpportunitySourceAdapter().collect(paths, NOW),
        MarketContextSourceAdapter().collect(paths, NOW),
        DaySourceAdapter().collect(paths, NOW),
    )

    # Then
    assert all(len(items) == 1 and ".blocked." not in items[0].source_key for items in evidence)
    assert all(items[0].payload_sha256 in items[0].evidence_refs for items in evidence)


def test_day_evidence_contains_existing_plan_checkpoint_and_immutable_outcome_history(
    tmp_path: Path,
) -> None:
    paths = source_paths(tmp_path)
    seed_day(paths)
    database = paths.day_session_root / "20260803" / "paper_recommendations.sqlite3"
    store = PaperStore(database)
    recommendation = Recommendation(
        recommendation_id="rec-acme-1",
        symbol="ACME",
        strategy="orb",
        created_at=NOW - dt.timedelta(seconds=50),
        entry=10.0,
        stop=9.5,
        target_1r=10.5,
        target_2r=11.0,
        state=RecommendationState.SETUP,
        rationale="completed bar breakout with bounded spread",
    )
    store.save(recommendation)
    store.set_state(
        recommendation.recommendation_id,
        RecommendationState.STOPPED,
        NOW - dt.timedelta(seconds=10),
        9.5,
        "same-bar collision resolved to stop",
    )
    database.chmod(0o600)

    evidence = DaySourceAdapter().collect(paths, NOW)[0]
    payload = json.loads(evidence.bounded_payload_json or "{}")

    assert payload["checkpoints"] == [
        {"last_close": 10.0, "processed_at": (NOW - dt.timedelta(minutes=1)).isoformat(), "symbol": "ACME"}
    ]
    assert payload["recommendations"] == [
        {
            "created_at": (NOW - dt.timedelta(seconds=50)).isoformat(),
            "entry": 10.0,
            "events": [
                {
                    "event_id": 1,
                    "note": "\ucd94\ucc9c \uc0dd\uc131",
                    "occurred_at": (NOW - dt.timedelta(seconds=50)).isoformat(),
                    "price": None,
                    "state": "setup",
                },
                {
                    "event_id": 2,
                    "note": "same-bar collision resolved to stop",
                    "occurred_at": (NOW - dt.timedelta(seconds=10)).isoformat(),
                    "price": 9.5,
                    "state": "stopped",
                },
            ],
            "rationale": "completed bar breakout with bounded spread",
            "recommendation_id": "rec-acme-1",
            "state": "stopped",
            "stop": 9.5,
            "strategy": "orb",
            "symbol": "ACME",
            "target_1r": 10.5,
            "target_2r": 11.0,
        }
    ]
    assert evidence.source_key in evidence.subject_refs
    assert len(evidence.subject_refs) == 4
    assert any(subject.startswith("day_recommendation.") for subject in evidence.subject_refs)
    assert sum(subject.startswith("day_event.") for subject in evidence.subject_refs) == 2


def test_breadth_producer_market_context_is_admitted_without_spread(tmp_path: Path) -> None:
    # Given
    paths = source_paths(tmp_path)
    seed_market_context(paths)

    # When
    evidence = MarketContextSourceAdapter().collect(paths, NOW)

    # Then
    assert len(evidence) == 1
    assert evidence[0].source_key.startswith("market_context.ctx.us_equities.")


def test_closed_session_blocks_all_primary_families_before_source_access(tmp_path: Path) -> None:
    # Given
    paths = source_paths(tmp_path)
    closed = dt.datetime(2026, 8, 3, 12, 0, tzinfo=dt.UTC)

    # When
    evidence = (
        OpportunitySourceAdapter().collect(paths, closed)[0],
        MarketContextSourceAdapter().collect(paths, closed)[0],
        DaySourceAdapter().collect(paths, closed)[0],
    )

    # Then
    assert tuple(item.source_key for item in evidence) == (
        "opportunity.blocked.session_closed",
        "market_context.blocked.session_closed",
        "day.blocked.session_closed",
    )


@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("opportunity", "opportunity.research_archive."),
        ("market_context", "market_context.research_archive."),
        ("day", "day.research_archive."),
    ],
)
def test_stale_primary_source_emits_family_blocked_evidence(
    tmp_path: Path,
    family: str,
    expected: str,
) -> None:
    # Given
    paths = source_paths(tmp_path)
    stale = NOW - dt.timedelta(minutes=4)
    if family == "opportunity":
        seed_opportunity(paths, observed_at=stale, valid_until=NOW - dt.timedelta(seconds=1))
        adapter = OpportunitySourceAdapter()
    elif family == "market_context":
        seed_market_context(paths, observed_at=stale, valid_until=NOW - dt.timedelta(seconds=1))
        adapter = MarketContextSourceAdapter()
    else:
        seed_day(paths, observed_at=stale)
        adapter = DaySourceAdapter()

    # When
    evidence = adapter.collect(paths, NOW)

    # Then
    assert len(evidence) == 1
    assert evidence[0].source_key.startswith(expected)


@pytest.mark.parametrize(
    ("family", "expected"),
    [
        ("opportunity", "opportunity.blocked.missing_spread"),
        ("day", "day.blocked.missing_spread"),
    ],
)
def test_missing_spread_emits_family_blocked_evidence(
    tmp_path: Path,
    family: str,
    expected: str,
) -> None:
    # Given
    paths = source_paths(tmp_path)
    if family == "opportunity":
        seed_opportunity(paths, spread=None)
        adapter = OpportunitySourceAdapter()
    else:
        seed_day(paths, spread="nan")
        adapter = DaySourceAdapter()

    # When
    evidence = adapter.collect(paths, NOW)

    # Then
    assert tuple(item.source_key for item in evidence) == (expected,)


def test_day_requires_database_and_risk_screen_as_one_source_pair(tmp_path: Path) -> None:
    # Given
    paths = source_paths(tmp_path)
    seed_day(paths)
    (paths.day_session_root / "20260803" / "market_risk_screen.csv").unlink()

    # When
    evidence = DaySourceAdapter().collect(paths, NOW)

    # Then
    assert tuple(item.source_key for item in evidence) == ("day.blocked.source_pair_unavailable",)


def test_day_rejects_mode_0644_paper_database_before_read(tmp_path: Path) -> None:
    # Given
    paths = source_paths(tmp_path)
    seed_day(paths)
    database = paths.day_session_root / "20260803" / "paper_recommendations.sqlite3"
    database.chmod(0o644)

    # When / Then
    with pytest.raises(InvalidResearchAgentSourceError, match="day_source_invalid"):
        DaySourceAdapter().collect(paths, NOW)
