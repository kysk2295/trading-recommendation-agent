from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from tests.strategy_research_source_hypothesis_fixtures import (
    NOW,
)
from tests.strategy_research_source_hypothesis_fixtures import (
    OpportunityOverrides as _OpportunityOverrides,
)
from tests.strategy_research_source_hypothesis_fixtures import (
    action_context as _action_context,
)
from tests.strategy_research_source_hypothesis_fixtures import (
    append_sources as _append_sources,
)
from tests.strategy_research_source_hypothesis_fixtures import (
    creator as _creator,
)
from tests.strategy_research_source_hypothesis_fixtures import (
    opportunity_evidence as _opportunity_evidence,
)
from tests.strategy_research_source_hypothesis_fixtures import (
    source_store as _source_store,
)
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.research_agent_primary_actions import OpportunityResearchActionExecutor
from trading_agent.strategy_research_evidence_service import (
    SourceHypothesisRequest,
    StrategyResearchEvidenceRejected,
)
from trading_agent.strategy_research_runtime_source import PrivateStrategyResearchWorkSource
from trading_agent.strategy_research_types import ResearchAgentId
from trading_agent.strategy_research_work_sink import PrivateStrategyResearchWorkSink


def test_current_immutable_source_creates_new_owner_hypothesis_without_card(tmp_path: Path) -> None:
    # Given: current immutable Opportunity and Market Context evidence in the real cycle store.
    with _source_store(tmp_path) as store:
        opportunity = _append_sources(store)
        creator = _creator(store)

        # When: the routed strategy owner consumes the source for the first time.
        artifact = creator.create(
            SourceHypothesisRequest(opportunity.evidence_id, ResearchAgentId.INTRADAY_MOMENTUM, NOW)
        )

        # Then: source, observation, and a new immutable owner hypothesis are exact artifacts.
        assert (
            artifact.candidate.source_id,
            artifact.observation.owner_agent_id,
            artifact.hypothesis.agent_id,
            artifact.hypothesis.primary_metric,
        ) == (
            "ranking:nas:1:acme",
            ResearchAgentId.INTRADAY_MOMENTUM,
            ResearchAgentId.INTRADAY_MOMENTUM,
            "studentized bootstrap mean net excess return",
        )
        assert {
            artifact.candidate.source_id,
            artifact.observation.observation_id,
            artifact.hypothesis.hypothesis_id,
        }.issubset(artifact.artifact_refs)


def test_production_action_returns_new_source_observation_and_hypothesis_refs(tmp_path: Path) -> None:
    # Given: production action dependencies read Opportunity and Market Context from one cycle store.
    with _source_store(tmp_path) as store:
        opportunity = _append_sources(store)
        context = _action_context(opportunity)

        # When: the Opportunity production action proposes a hypothesis with no legacy card resolver.
        result = OpportunityResearchActionExecutor(hypothesis_creator=_creator(store)).execute(context)

        # Then: the completed action exposes all newly created immutable lineage identities.
        assert result.artifact_refs == tuple(sorted(result.artifact_refs))
        assert "ranking:nas:1:acme" in result.artifact_refs
        assert any(item.startswith("observation-intraday_momentum-") for item in result.artifact_refs)
        assert any(item.startswith("hypothesis-intraday_momentum-") for item in result.artifact_refs)


@pytest.mark.parametrize("failure", ["stale", "missing_context", "prior_session"])
def test_invalid_live_evidence_rejects_without_hypothesis(tmp_path: Path, failure: str) -> None:
    # Given: a malformed live-evidence state in an otherwise real cycle store.
    with _source_store(tmp_path) as store:
        opportunity = _append_sources(store, failure=failure)
        creator = _creator(store)

        # When/Then: the typed fail-closed boundary rejects before returning any hypothesis.
        with pytest.raises(StrategyResearchEvidenceRejected) as captured:
            creator.create(SourceHypothesisRequest(opportunity.evidence_id, ResearchAgentId.INTRADAY_MOMENTUM, NOW))
        assert captured.value.reason in {
            "market_context_missing",
            "opportunity_not_current",
            "opportunity_stale",
        }


def test_replay_is_idempotent_and_distinct_source_has_distinct_hypothesis(tmp_path: Path) -> None:
    # Given: two distinct immutable Opportunity sources sharing current Market Context.
    with _source_store(tmp_path) as store:
        first = _append_sources(store)
        second = _opportunity_evidence("us-opportunity-20260819t145900-efgh5678", "BETA")
        assert store.append_evidence(second)
        creator = _creator(store)

        # When: one source is replayed and another source is consumed once.
        request = SourceHypothesisRequest(first.evidence_id, ResearchAgentId.INTRADAY_MOMENTUM, NOW)
        original = creator.create(request)
        replay = creator.create(request)
        distinct = creator.create(SourceHypothesisRequest(second.evidence_id, ResearchAgentId.INTRADAY_MOMENTUM, NOW))

        # Then: replay identity is stable while source novelty changes the hypothesis identity.
        assert replay == original
        assert distinct.hypothesis.hypothesis_id != original.hypothesis.hypothesis_id


def test_older_source_rejects_when_newer_completed_source_exists(tmp_path: Path) -> None:
    # Given: a current but older source is appended after a newer completed source.
    with _source_store(tmp_path) as store:
        _ = _append_sources(store)
        older = _opportunity_evidence(
            "us-opportunity-20260819t145800-older001",
            "BETA",
            _OpportunityOverrides(NOW - dt.timedelta(minutes=2), NOW + dt.timedelta(minutes=4)),
        )
        assert store.append_evidence(older)

        # When/Then: latest-completed-source admission rejects the older identity.
        with pytest.raises(StrategyResearchEvidenceRejected, match="opportunity_not_latest"):
            _creator(store).create_routed(older.evidence_id, NOW)


def test_owner_mismatch_and_prompt_injection_cannot_change_science_authority(tmp_path: Path) -> None:
    # Given: untrusted evidence text asks to replace metrics and grant authority.
    with _source_store(tmp_path) as store:
        opportunity = _append_sources(store, injected=True)
        creator = _creator(store)

        # When/Then: routing rejects the wrong owner.
        with pytest.raises(StrategyResearchEvidenceRejected, match="owner_mismatch"):
            creator.create(SourceHypothesisRequest(opportunity.evidence_id, ResearchAgentId.CATALYST_EVENT, NOW))

        # When: the correct deterministic owner consumes the same untrusted evidence.
        artifact = creator.create(
            SourceHypothesisRequest(opportunity.evidence_id, ResearchAgentId.INTRADAY_MOMENTUM, NOW)
        )

        # Then: evidence remains data and cannot select metric, holdout, or trading authority.
        assert (
            artifact.hypothesis.primary_metric,
            artifact.hypothesis.holdout_period_sealed_ref.owner,
            artifact.hypothesis.trading_authority,
        ) == ("studentized bootstrap mean net excess return", "science-kernel", False)


def test_source_hypothesis_sink_preregisters_and_queues_future_maturity(tmp_path: Path) -> None:
    with _source_store(tmp_path) as store:
        opportunity = _append_sources(store)
        artifact = _creator(store).create_routed(opportunity.evidence_id, NOW)
        ledger = ExperimentLedgerStore(tmp_path / "experiment.sqlite3")
        root = tmp_path / "work"

        created = PrivateStrategyResearchWorkSink(ledger, root).persist(artifact)
        replayed = PrivateStrategyResearchWorkSink(ledger, root).persist(artifact)
        work = PrivateStrategyResearchWorkSource(root).next_work(artifact.hypothesis.agent_id, None)

    assert created is True
    assert replayed is False
    assert work is not None
    assert work.draft.hypothesis_id == artifact.hypothesis.hypothesis_id
    assert work.maturity_at == artifact.hypothesis.target_matures_at
    assert work.experiment is None
    assert len(ExperimentLedgerReader(ledger.path).strategy_research_preregistrations()) == 1
