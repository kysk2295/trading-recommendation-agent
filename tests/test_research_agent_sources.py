from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from trading_agent.contract_outbox import append_opportunity_snapshot
from trading_agent.dashboard_agent_family import PRIMARY_AGENT_FAMILIES
from trading_agent.market_context_breadth_producer import (
    BreadthMemberObservation,
    produce_market_context_from_breadth,
)
from trading_agent.research_agent_cycle_models import ResearchAgentTriggerKind
from trading_agent.research_agent_cycle_store import ResearchAgentCycleStore
from trading_agent.research_agent_source_adapters_primary import OpportunitySourceAdapter
from trading_agent.research_agent_source_adapters_research import SwingSourceAdapter
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


def test_opportunity_prior_date_emits_family_blocked_evidence(tmp_path: Path) -> None:
    # Given: the only Opportunity snapshot belongs to the prior NY session.
    paths = _source_paths(tmp_path)
    _seed_opportunity(paths)
    current_session = dt.datetime(2026, 8, 4, 14, 31, tzinfo=dt.UTC)

    # When: the Primary adapter inspects the current open session.
    evidence = OpportunitySourceAdapter().collect(paths, current_session)

    # Then: prior-date data is explicit blocked evidence, never admitted research input.
    assert tuple(item.source_key for item in evidence) == ("opportunity.blocked.prior_date",)


def test_swing_rejects_mode_0644_shadow_ledger_before_projection(tmp_path: Path) -> None:
    # Given: an initialized but non-private Swing shadow ledger.
    paths = _source_paths(tmp_path)
    with SwingShadowStore(paths.swing_shadow_database).writer():
        pass
    paths.swing_shadow_database.chmod(0o644)

    # When / Then: the Research adapter refuses it before producing blocked evidence.
    with pytest.raises(InvalidResearchAgentSourceError, match="swing_source_invalid"):
        SwingSourceAdapter().collect(paths, NOW)
