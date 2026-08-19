from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES
from trading_agent.day_discovery_loop import DayDiscoveryEvidenceView, DayDiscoveryTriggerKind
from trading_agent.market_context_breadth_producer import (
    BreadthMemberObservation,
    produce_market_context_from_breadth,
)
from trading_agent.research_agent_cycle_models import ResearchAgentTriggerKind
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_source_adapters_primary import (
    DaySourceAdapter,
    MarketContextSourceAdapter,
    OpportunitySourceAdapter,
    bounded_day_discovery_feedback,
)
from trading_agent.research_agent_source_adapters_research import SwingSourceAdapter
from trading_agent.research_agent_source_common import canonical_model_json
from trading_agent.research_agent_sources import (
    InvalidResearchAgentSourceError,
    ResearchAgentSourcePaths,
    collect_research_agent_evidence,
)
from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.signal_contract_models import (
    EvidenceRef,
    FeatureValue,
    OpportunityCandidate,
    OpportunitySnapshot,
    SourceCoverage,
)
from trading_agent.swing_shadow_store import SwingShadowStore

NOW = dt.datetime(2026, 8, 3, 14, 35, tzinfo=dt.UTC)


def _source_paths(tmp_path: Path) -> ResearchAgentSourcePaths:
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    return ResearchAgentSourcePaths(
        outputs_root=outputs,
        market_context_root=outputs / "market_context",
        day_session_root=outputs / "live_sessions",
        swing_shadow_database=outputs / "swing" / "shadow.sqlite3",
        swing_review_database=outputs / "swing" / "review.sqlite3",
        experiment_ledger=outputs / "experiment_control" / "experiment_ledger.sqlite3",
        lane_review_database=outputs / "lane_control" / "lane_review.sqlite3",
    )


def _seed_opportunity(paths: ResearchAgentSourcePaths) -> None:
    observed_at = NOW - dt.timedelta(minutes=1)
    snapshot = OpportunitySnapshot(
        opportunity_id="us-opportunity-20260803t143400-abcd1234",
        strategy_lane=StrategyLaneRef(
            market_id=MarketId.US_EQUITIES,
            agent_family=AgentFamily.OPPORTUNITY_MANAGER,
            strategy_id="ranking_momentum",
        ),
        producer_strategy_version="ranking-momentum-v1",
        observed_at=observed_at,
        valid_until=NOW + dt.timedelta(minutes=1),
        candidates=(
            OpportunityCandidate(
                symbol="ACME",
                rank=1,
                score=Decimal("0.12"),
                features=(
                    FeatureValue(name="change_pct", value="0.12"),
                    FeatureValue(name="spread_bps", value="12.5"),
                ),
            ),
        ),
        evidence_refs=(EvidenceRef(namespace="ranking", record_id="nas:1:acme", observed_at=observed_at),),
        source_coverage=(
            SourceCoverage(source_id="ranking_source", observed_at=observed_at, record_count=1, complete=True),
        ),
    )
    session = paths.day_session_root / "20260803"
    session.mkdir(parents=True)
    assert append_opportunity_snapshot(session / "opportunities.v1.jsonl", snapshot)


def _seed_market_context(paths: ResearchAgentSourcePaths) -> None:
    paths.market_context_root.mkdir(parents=True)
    snapshot = produce_market_context_from_breadth(
        (
            BreadthMemberObservation("AAPL", 120, 10_000),
            BreadthMemberObservation("MSFT", -50, 8_000),
        ),
        market_id=MarketId.US_EQUITIES,
        observed_at=NOW,
        valid_until=NOW + dt.timedelta(minutes=30),
    )
    path = paths.market_context_root / "us-current.market-context.json"
    path.write_text(snapshot.model_dump_json(), encoding="utf-8")
    path.chmod(0o600)


def test_source_projection_routes_evidence_without_cross_family_leakage(tmp_path: Path) -> None:
    paths = _source_paths(tmp_path)
    _seed_opportunity(paths)
    _seed_market_context(paths)

    projected = collect_research_agent_evidence(paths, now=NOW)
    by_family = {
        family: tuple(item for item in projected if item.agent_family_id == family) for family in PRIMARY_AGENT_FAMILIES
    }

    assert {family for family, items in by_family.items() if items} == set(PRIMARY_AGENT_FAMILIES)
    assert all(item.payload_sha256 in item.evidence_refs for item in by_family["systematic_quant"])
    assert all(item.market_id == "none" for item in by_family["systematic_quant"])
    assert all(item.trigger_kind is ResearchAgentTriggerKind.MARKET_EVENT for item in by_family["market_context"])
    opportunity = next(item for item in projected if item.agent_family_id == "opportunity_manager")
    payload = json.loads(opportunity.bounded_payload_json or "{}")
    candidate_ref = f"opportunity_candidate.{hashlib.sha256(opportunity.source_key.encode()).hexdigest()[:16]}.1"
    assert "ACME" in json.dumps(payload)
    assert opportunity.subject_refs == tuple(sorted((opportunity.source_key, candidate_ref)))
    assert opportunity.bounded_payload_json is not None
    assert opportunity.payload_sha256 == hashlib.sha256(opportunity.bounded_payload_json.encode()).hexdigest()


def test_missing_derivatives_entitlement_is_explicit_evidence(tmp_path: Path) -> None:
    projected = collect_research_agent_evidence(_source_paths(tmp_path), now=NOW)

    derivative = next(item for item in projected if item.agent_family_id == "derivatives_research")

    assert derivative.source_key == "derivatives.blocked.options_entitlement_missing"


def test_unchanged_derivatives_state_does_not_create_per_tick_evidence(tmp_path: Path) -> None:
    paths = _source_paths(tmp_path)

    first = collect_research_agent_evidence(paths, now=NOW)
    second = collect_research_agent_evidence(paths, now=NOW + dt.timedelta(seconds=30))
    first_derivative = next(item for item in first if item.agent_family_id == "derivatives_research")
    second_derivative = next(item for item in second if item.agent_family_id == "derivatives_research")

    assert first_derivative.evidence_id == second_derivative.evidence_id


def test_unchanged_derivatives_state_creates_one_evidence_per_interval_bucket(tmp_path: Path) -> None:
    paths = _source_paths(tmp_path)
    first = collect_research_agent_evidence(paths, now=NOW)
    next_interval = collect_research_agent_evidence(paths, now=NOW + dt.timedelta(minutes=15))
    first_derivative = next(item for item in first if item.agent_family_id == "derivatives_research")
    next_derivative = next(item for item in next_interval if item.agent_family_id == "derivatives_research")
    with ResearchAgentCycleStore(tmp_path / "cycles.sqlite3") as store:
        assert store.append_evidence(first_derivative)
        assert store.append_evidence(next_derivative)
    assert first_derivative.evidence_id != next_derivative.evidence_id


def test_malformed_existing_market_context_fails_the_collection_tick(tmp_path: Path) -> None:
    paths = _source_paths(tmp_path)
    paths.market_context_root.mkdir(parents=True)
    malformed = paths.market_context_root / "broken.market-context.json"
    malformed.write_text("{}", encoding="utf-8")
    malformed.chmod(0o600)

    with pytest.raises(InvalidResearchAgentSourceError, match="market_context_source_invalid"):
        collect_research_agent_evidence(paths, now=NOW)


def test_source_paths_reject_relative_or_symlinked_boundaries(tmp_path: Path) -> None:
    paths = _source_paths(tmp_path)
    alias = tmp_path / "outputs-alias"
    alias.symlink_to(paths.outputs_root)

    with pytest.raises(ValidationError, match="source_path_invalid"):
        ResearchAgentSourcePaths(
            **(paths.model_dump(mode="python") | {"outputs_root": alias}),
        )


def test_opportunity_prior_date_becomes_research_archive_evidence(tmp_path: Path) -> None:
    # Given: the only Opportunity snapshot belongs to the prior NY session.
    paths = _source_paths(tmp_path)
    _seed_opportunity(paths)
    current_session = dt.datetime(2026, 8, 4, 14, 31, tzinfo=dt.UTC)

    # When: the Primary adapter inspects the current open session.
    evidence = OpportunitySourceAdapter().collect(paths, current_session)

    # Then: it remains usable as explicitly archived research input.
    assert tuple(item.source_key for item in evidence) == (
        "opportunity.research_archive.us-opportunity-20260803t143400-abcd1234",
    )
    expected = evidence[0]
    candidate_ref = f"opportunity_candidate.{hashlib.sha256(expected.source_key.encode()).hexdigest()[:16]}.1"
    assert expected.subject_refs == tuple(sorted((expected.source_key, candidate_ref)))


def test_expired_market_context_becomes_research_archive_evidence(tmp_path: Path) -> None:
    paths = _source_paths(tmp_path)
    _seed_market_context(paths)

    evidence = MarketContextSourceAdapter().collect(paths, NOW + dt.timedelta(days=1))

    assert len(evidence) == 1
    assert evidence[0].source_key.startswith("market_context.research_archive.")


def test_prior_day_pair_becomes_research_archive_evidence(tmp_path: Path) -> None:
    paths = _source_paths(tmp_path)
    from tests.research_agent_primary_fixtures import seed_day

    seed_day(paths)
    evidence = DaySourceAdapter().collect(paths, NOW + dt.timedelta(days=1))

    assert tuple(item.source_key for item in evidence) == ("day.research_archive.20260803",)


def test_latest_complete_day_pair_skips_newer_incomplete_session(tmp_path: Path) -> None:
    paths = _source_paths(tmp_path)
    from tests.research_agent_primary_fixtures import seed_day

    seed_day(paths)
    (paths.day_session_root / "20260804").mkdir()

    evidence = DaySourceAdapter().collect(paths, NOW + dt.timedelta(days=2))

    assert tuple(item.source_key for item in evidence) == ("day.research_archive.20260803",)


def test_day_discovery_collection_is_stable_and_preserves_recommendation_evidence(
    tmp_path: Path,
) -> None:
    paths = _source_paths(tmp_path)
    from tests.research_agent_primary_fixtures import seed_day

    seed_day(paths)
    session = paths.day_session_root / "20260803"
    fixture = json.loads((Path(__file__).parent / "fixtures/day-research/discovery-evidence.json").read_text())
    fixture.update(
        {
            "observed_at": NOW.isoformat(),
            "completed_bar_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
            "first_eligible_completed_bar_at": (NOW + dt.timedelta(minutes=1)).isoformat(),
            "universe_snapshot_at": (NOW - dt.timedelta(minutes=2)).isoformat(),
            "replay_bars": [fixture["replay_bars"][0] | {"timestamp": (NOW - dt.timedelta(minutes=2)).isoformat()}],
        }
    )
    view = DayDiscoveryEvidenceView.model_validate(fixture)
    artifact = session / "day-discovery-evidence.us_equities.v1.json"
    artifact.write_text(canonical_model_json(view), encoding="utf-8")
    artifact.chmod(0o600)

    first = DaySourceAdapter().collect(paths, NOW)
    second = DaySourceAdapter().collect(paths, NOW + dt.timedelta(seconds=20))
    first_discovery = next(item for item in first if item.source_key.startswith("day.discovery."))
    second_discovery = next(item for item in second if item.source_key.startswith("day.discovery."))

    assert first_discovery == second_discovery
    assert first_discovery.available_at == view.observed_at
    assert any(item.source_key.startswith("day.session.") for item in first)
    with ResearchAgentCycleStore(tmp_path / "day-cycles.sqlite3") as store:
        assert store.append_evidence(first_discovery)
        assert not store.append_evidence(second_discovery)


def test_canonical_day_discovery_feedback_is_wired_into_source_payload(
    tmp_path: Path,
) -> None:
    paths = _source_paths(tmp_path)
    session = paths.day_session_root / "20260803"
    session.mkdir(parents=True)
    fixture = json.loads((Path(__file__).parent / "fixtures/day-research/discovery-evidence.json").read_text())
    fixture.update(
        {
            "trigger_kind": DayDiscoveryTriggerKind.REVIEW_CLOSE,
            "observed_at": NOW.isoformat(),
            "completed_bar_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
            "first_eligible_completed_bar_at": (NOW + dt.timedelta(minutes=1)).isoformat(),
            "universe_snapshot_at": (NOW - dt.timedelta(minutes=2)).isoformat(),
            "replay_bars": [fixture["replay_bars"][0] | {"timestamp": (NOW - dt.timedelta(minutes=2)).isoformat()}],
        }
    )
    view = DayDiscoveryEvidenceView.model_validate(fixture)
    evidence_path = session / "day-discovery-evidence.us_equities.v1.json"
    evidence_path.write_text(canonical_model_json(view), encoding="utf-8")
    evidence_path.chmod(0o600)
    feedback_path = session / "day-discovery-feedback.us_equities.v1.json"
    feedback_path.write_text(
        bounded_day_discovery_feedback(
            {
                "outcome_class": "inconclusive",
                "bounded_metrics": {"signal_count": 2},
                "remaining_budget": 2,
                "runtime_reason": "sandbox_failed",
            }
        ),
        encoding="utf-8",
    )
    feedback_path.chmod(0o600)

    evidence = next(
        item for item in DaySourceAdapter().collect(paths, NOW) if item.source_key.startswith("day.discovery.")
    )
    payload = json.loads(evidence.bounded_payload_json or "{}")
    assert payload["feedback"]["remaining_budget"] == 2
    assert payload["feedback"]["runtime_reason"] == "sandbox_failed"


def test_day_discovery_collects_one_current_artifact_for_each_market(
    tmp_path: Path,
) -> None:
    paths = _source_paths(tmp_path)
    session = paths.day_session_root / "20260803"
    session.mkdir(parents=True)
    fixture = json.loads((Path(__file__).parent / "fixtures/day-research/discovery-evidence.json").read_text())
    fixture.update(
        {
            "observed_at": NOW.isoformat(),
            "completed_bar_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
            "first_eligible_completed_bar_at": (NOW + dt.timedelta(minutes=1)).isoformat(),
            "universe_snapshot_at": (NOW - dt.timedelta(minutes=2)).isoformat(),
            "replay_bars": [fixture["replay_bars"][0] | {"timestamp": (NOW - dt.timedelta(minutes=2)).isoformat()}],
        }
    )
    us_view = DayDiscoveryEvidenceView.model_validate(fixture)
    kr_view = us_view.model_copy(
        update={
            "market_id": MarketId.KR_EQUITIES,
            "universe_snapshot_id": "fixture-kr-universe",
            "cursor": "kr:1",
        }
    )
    for market_id, view in (
        ("us_equities", us_view),
        ("kr_equities", kr_view),
    ):
        artifact = session / f"day-discovery-evidence.{market_id}.v1.json"
        artifact.write_text(canonical_model_json(view), encoding="utf-8")
        artifact.chmod(0o600)

    evidence = tuple(
        item for item in DaySourceAdapter().collect(paths, NOW) if item.source_key.startswith("day.discovery.")
    )
    assert tuple(item.market_id for item in evidence) == ("kr_equities", "us_equities")


def test_malformed_us_discovery_is_market_scoped_and_does_not_block_kr(
    tmp_path: Path,
) -> None:
    paths = _source_paths(tmp_path)
    session = paths.day_session_root / "20260803"
    session.mkdir(parents=True)
    fixture = json.loads((Path(__file__).parent / "fixtures/day-research/discovery-evidence.json").read_text())
    fixture.update(
        {
            "market_id": "kr_equities",
            "universe_snapshot_id": "fixture-kr-universe",
            "observed_at": NOW.isoformat(),
            "completed_bar_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
            "first_eligible_completed_bar_at": (NOW + dt.timedelta(minutes=1)).isoformat(),
            "universe_snapshot_at": (NOW - dt.timedelta(minutes=2)).isoformat(),
            "replay_bars": [fixture["replay_bars"][0] | {"timestamp": (NOW - dt.timedelta(minutes=2)).isoformat()}],
        }
    )
    kr_view = DayDiscoveryEvidenceView.model_validate(fixture)
    kr_artifact = session / "day-discovery-evidence.kr_equities.v1.json"
    kr_artifact.write_text(canonical_model_json(kr_view), encoding="utf-8")
    kr_artifact.chmod(0o600)
    us_artifact = session / "day-discovery-evidence.us_equities.v1.json"
    us_artifact.write_text("{malformed", encoding="utf-8")
    us_artifact.chmod(0o600)

    evidence = DaySourceAdapter().collect(paths, NOW)

    assert any(item.market_id == "kr_equities" and item.source_key.startswith("day.discovery.") for item in evidence)
    assert any(
        item.market_id == "us_equities" and item.source_key.startswith("day.blocked.day_discovery_source_invalid")
        for item in evidence
    )
    with ResearchAgentCycleStore(tmp_path / "malformed-source-cycles.sqlite3") as store:
        for item in evidence:
            assert store.append_evidence(item)
        kr_stored = next(
            item
            for item in store.runnable_evidence("day_trading", NOW)
            if item.evidence.market_id == "kr_equities"
        )
        assert store.start_cycle(kr_stored, NOW).market_id == "kr_equities"


def test_feedback_sidecar_does_not_bypass_base_evidence_canonicality(
    tmp_path: Path,
) -> None:
    paths = _source_paths(tmp_path)
    session = paths.day_session_root / "20260803"
    session.mkdir(parents=True)
    fixture = json.loads((Path(__file__).parent / "fixtures/day-research/discovery-evidence.json").read_text())
    fixture.update(
        {
            "observed_at": NOW.isoformat(),
            "completed_bar_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
            "first_eligible_completed_bar_at": (NOW + dt.timedelta(minutes=1)).isoformat(),
            "universe_snapshot_at": (NOW - dt.timedelta(minutes=2)).isoformat(),
            "replay_bars": [fixture["replay_bars"][0] | {"timestamp": (NOW - dt.timedelta(minutes=2)).isoformat()}],
        }
    )
    view = DayDiscoveryEvidenceView.model_validate(fixture)
    evidence_path = session / "day-discovery-evidence.us_equities.v1.json"
    evidence_path.write_text(f" {canonical_model_json(view)}", encoding="utf-8")
    evidence_path.chmod(0o600)
    feedback_path = session / "day-discovery-feedback.us_equities.v1.json"
    feedback_path.write_text('{"remaining_budget":1}', encoding="utf-8")
    feedback_path.chmod(0o600)

    evidence = DaySourceAdapter().collect(paths, NOW)

    assert not any(item.source_key.startswith("day.discovery.") for item in evidence)
    assert any(item.source_key.startswith("day.blocked.day_discovery_source_noncanonical") for item in evidence)


def test_pretty_feedback_sidecar_is_blocked_instead_of_normalized(
    tmp_path: Path,
) -> None:
    paths = _source_paths(tmp_path)
    session = paths.day_session_root / "20260803"
    session.mkdir(parents=True)
    fixture = json.loads((Path(__file__).parent / "fixtures/day-research/discovery-evidence.json").read_text())
    fixture.update(
        {
            "observed_at": NOW.isoformat(),
            "completed_bar_at": (NOW - dt.timedelta(minutes=1)).isoformat(),
            "first_eligible_completed_bar_at": (NOW + dt.timedelta(minutes=1)).isoformat(),
            "universe_snapshot_at": (NOW - dt.timedelta(minutes=2)).isoformat(),
            "replay_bars": [fixture["replay_bars"][0] | {"timestamp": (NOW - dt.timedelta(minutes=2)).isoformat()}],
        }
    )
    view = DayDiscoveryEvidenceView.model_validate(fixture)
    evidence_path = session / "day-discovery-evidence.us_equities.v1.json"
    evidence_path.write_text(canonical_model_json(view), encoding="utf-8")
    evidence_path.chmod(0o600)
    feedback_path = session / "day-discovery-feedback.us_equities.v1.json"
    canonical = bounded_day_discovery_feedback({"remaining_budget": 1})
    feedback_path.write_text(json.dumps(json.loads(canonical), indent=2), encoding="utf-8")
    feedback_path.chmod(0o600)

    evidence = DaySourceAdapter().collect(paths, NOW)

    assert not any(item.source_key.startswith("day.discovery.") for item in evidence)
    assert any(
        item.source_key.startswith("day.blocked.day_discovery_feedback_noncanonical")
        for item in evidence
    )


def test_swing_rejects_mode_0644_shadow_ledger_before_projection(tmp_path: Path) -> None:
    # Given: an initialized but non-private Swing shadow ledger.
    paths = _source_paths(tmp_path)
    with SwingShadowStore(paths.swing_shadow_database).writer():
        pass
    paths.swing_shadow_database.chmod(0o644)

    # When / Then: the Research adapter refuses it before producing blocked evidence.
    with pytest.raises(InvalidResearchAgentSourceError, match="swing_source_invalid"):
        SwingSourceAdapter().collect(paths, NOW)
