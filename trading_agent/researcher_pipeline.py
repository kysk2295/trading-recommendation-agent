from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never, override

from trading_agent.critic_agent import CritiqueReport, HypothesisCritic
from trading_agent.experiment_ledger_keys import research_source_key
from trading_agent.experiment_ledger_models import StrategyLifecycleState, TrialEventKind
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.generated_strategy_artifact import (
    GeneratedStrategyArtifactStore,
    PublishedGeneratedStrategy,
)
from trading_agent.private_immutable_file import publish_private_immutable_text
from trading_agent.research_hypothesis_registration import (
    ResearchHypothesisManifest,
    register_research_hypothesis_manifest,
)
from trading_agent.researcher_agent import FailureDigest, HypothesisGenerator, ProposedHypothesis, ResearcherContext
from trading_agent.researcher_llm import ResearcherContextInput
from trading_agent.researcher_receipt_store import ResearcherReceiptStore
from trading_agent.source_driven_hypothesis_queue import (
    project_source_driven_hypothesis_queue,
    publish_source_driven_hypothesis_queue,
)
from trading_agent.strategy_research_evidence_service import (
    CycleStoreMarketContextEvidenceService,
    CycleStoreOpportunityEvidenceService,
    EvidenceQuery,
    KisKrMarketSessionGate,
    UsOnlyMarketSessionGate,
)
from trading_agent.strategy_research_hypothesis_factory import StrategyResearchHypothesisFactory


class ResearcherPipelineError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "researcher proposal pipeline failed closed"


@dataclass(frozen=True, slots=True)
class ResearcherPipelineServices:
    generator: HypothesisGenerator
    critic: HypothesisCritic


@dataclass(frozen=True, slots=True)
class ResearcherPipelineStores:
    ledger: ExperimentLedgerStore
    receipts: ResearcherReceiptStore
    strategies: GeneratedStrategyArtifactStore


@dataclass(frozen=True, slots=True)
class ResearcherPipelineArtifacts:
    manifest_root: Path
    queue_root: Path


@dataclass(frozen=True, slots=True)
class AcceptedResearchProposal:
    proposal: ProposedHypothesis
    critique: CritiqueReport
    strategy_artifact: PublishedGeneratedStrategy
    manifest_path: Path
    queue_path: Path


@dataclass(frozen=True, slots=True)
class DroppedResearchProposal:
    critiques: tuple[CritiqueReport, ...]


type ResearcherPipelineResult = AcceptedResearchProposal | DroppedResearchProposal


@dataclass(frozen=True, slots=True)
class ResearcherPipeline:
    services: ResearcherPipelineServices
    stores: ResearcherPipelineStores
    artifacts: ResearcherPipelineArtifacts

    def propose_candidate(
        self,
        context: ResearcherContext,
        supplemental_critic: Callable[[ProposedHypothesis], CritiqueReport] | None = None,
    ) -> tuple[ProposedHypothesis, CritiqueReport]:
        proposal = self.services.generator.propose(context)
        base = self.services.critic.critique(proposal, self.stores.ledger)
        supplemental = CritiqueReport(()) if supplemental_critic is None else supplemental_critic(proposal)
        critique = CritiqueReport(base.objections + supplemental.objections)
        _ = self.stores.receipts.record_critique(proposal, critique)
        return proposal, critique

    def run(self, context: ResearcherContext, *, max_attempts: int) -> ResearcherPipelineResult:
        if max_attempts < 1 or max_attempts > 3:
            raise ResearcherPipelineError
        critiques: list[CritiqueReport] = []
        for _ in range(max_attempts):
            proposal, critique = self.propose_candidate(context)
            critiques.append(critique)
            if not critique.is_blocked:
                strategy_artifact = self.stores.strategies.publish(proposal)
                manifest = _manifest(proposal)
                manifest_path = (
                    self.artifacts.manifest_root.resolve(strict=False)
                    / f"research_hypothesis_{proposal.llm_receipt.response_sha256}.json"
                )
                _ = publish_private_immutable_text(manifest_path, manifest.model_dump_json())
                _ = register_research_hypothesis_manifest(manifest_path, self.stores.ledger)
                queue = project_source_driven_hypothesis_queue(
                    ExperimentLedgerReader(self.stores.ledger.path)
                )
                queue_path, _ = publish_source_driven_hypothesis_queue(
                    self.artifacts.queue_root.resolve(strict=False),
                    queue,
                )
                return AcceptedResearchProposal(
                    proposal,
                    critique,
                    strategy_artifact,
                    manifest_path,
                    queue_path,
                )
        return DroppedResearchProposal(tuple(critiques))


def build_source_hypothesis_factory(
    query: EvidenceQuery,
    kr_calendar_store: Path | None = None,
) -> StrategyResearchHypothesisFactory:
    sessions = (
        UsOnlyMarketSessionGate()
        if kr_calendar_store is None
        else KisKrMarketSessionGate(kr_calendar_store)
    )
    return StrategyResearchHypothesisFactory(
        CycleStoreOpportunityEvidenceService(query, sessions),
        CycleStoreMarketContextEvidenceService(query, sessions),
    )


def build_researcher_context(
    source: ResearcherContextInput,
    ledger: ExperimentLedgerReader,
) -> ResearcherContext:
    cards = ledger.research_hypothesis_cards()
    cards_by_hypothesis = {
        stored.card.hypothesis.hypothesis_id: stored.card
        for stored in cards
    }
    versions = ledger.strategy_versions()
    versions_by_name = {
        stored.registration.strategy_version: stored.registration
        for stored in versions
    }
    censored_reasons: set[str] = set()
    failed_falsifications: set[str] = set()
    for stored_trial in ledger.trials():
        events = ledger.trial_events(stored_trial.registration.trial_id)
        if not events:
            continue
        terminal = events[-1].event
        match terminal.event_kind:
            case TrialEventKind.CENSORED:
                censored_reasons.update(terminal.reason_codes)
            case TrialEventKind.FAILED:
                version = versions_by_name[stored_trial.registration.strategy_version]
                card = cards_by_hypothesis[version.hypothesis_id]
                failed_falsifications.add(card.hypothesis.falsification_rule)
            case TrialEventKind.STARTED | TrialEventKind.COMPLETED:
                pass
            case unexpected:
                assert_never(unexpected)
    rejected_ids = {
        stored.registration.hypothesis_id
        for stored in versions
        if _rejected(stored.registration.strategy_version, ledger)
    }
    rejected_texts = {
        stored.card.hypothesis.hypothesis
        for stored in cards
        if stored.card.hypothesis.hypothesis_id in rejected_ids
    }
    return ResearcherContext(
        lane_id=source.lane_id,
        sources=source.sources,
        failure_digest=FailureDigest(
            tuple(sorted(censored_reasons)),
            tuple(sorted(failed_falsifications)),
            tuple(sorted(rejected_texts)),
        ),
        regime_context=source.regime_context,
        existing_hypothesis_texts=tuple(
            sorted(stored.card.hypothesis.hypothesis for stored in cards)
        ),
    )


def _manifest(proposal: ProposedHypothesis) -> ResearchHypothesisManifest:
    card = proposal.card
    source_keys = tuple(sorted(str(research_source_key(source)) for source in proposal.cited_sources))
    if source_keys != card.research_source_keys:
        raise ResearcherPipelineError
    return ResearchHypothesisManifest(
        research_sources=proposal.cited_sources,
        experiment_scope=card.hypothesis.experiment_scope,
        hypothesis=card.hypothesis.hypothesis,
        falsification_rule=card.hypothesis.falsification_rule,
        research_source_ids=tuple(sorted(source.source_id for source in proposal.cited_sources)),
        economic_mechanism=card.economic_mechanism,
        counterfactual_baseline=card.counterfactual_baseline,
        ledger_recorded_at=card.hypothesis.ledger_recorded_at,
    )


def _rejected(strategy_version: str, ledger: ExperimentLedgerReader) -> bool:
    events = ledger.lifecycle_events(strategy_version)
    return bool(events) and events[-1].event.to_state is StrategyLifecycleState.REJECTED


__all__ = (
    "AcceptedResearchProposal",
    "DroppedResearchProposal",
    "ResearcherPipeline",
    "ResearcherPipelineArtifacts",
    "ResearcherPipelineError",
    "ResearcherPipelineResult",
    "ResearcherPipelineServices",
    "ResearcherPipelineStores",
    "build_researcher_context",
    "build_source_hypothesis_factory",
)
