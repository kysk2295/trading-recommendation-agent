from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from pathlib import Path
from typing import assert_never, override

from trading_agent.data_foundation_manifest import load_data_foundation_artifact
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.generated_intraday_loop import (
    GeneratedIntradayLoopPaths,
    GeneratedIntradayLoopResult,
    run_generated_intraday_loop,
)
from trading_agent.generated_intraday_research_models import (
    GeneratedIntradayResearchManifest,
    GeneratedStrategySelection,
)
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.replay import load_bounded_bar_source
from trading_agent.researcher_agent import FailureDigest, ResearcherContext
from trading_agent.researcher_llm import ResearcherContextInput
from trading_agent.researcher_pipeline import (
    AcceptedResearchProposal,
    DroppedResearchProposal,
    ResearcherPipeline,
    build_researcher_context,
)
from trading_agent.source_driven_hypothesis_queue import load_source_driven_hypothesis_queue


class AutonomousResearchCycleError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "bounded autonomous research cycle failed closed"


@dataclass(frozen=True, slots=True)
class AutonomousResearchCycleConfig:
    source: ResearcherContextInput
    pipeline: ResearcherPipeline
    ledger: ExperimentLedgerStore
    sandbox: GeneratedStrategySandbox
    input_csv: Path
    data_foundation_manifest: Path
    experiment_root: Path
    review_root: Path
    max_attempts: int = 2
    minimum_training_sessions: int = 0
    max_bars: int = 100_000
    max_sessions: int = 60
    per_side_fee_bps: int = 5
    per_side_slippage_bps: int = 15
    bootstrap_samples: int = 200
    rss_limit_gib: float = 9.5


@dataclass(frozen=True, slots=True)
class AutonomousResearchCycleResult:
    accepted: AcceptedResearchProposal
    historical: GeneratedIntradayLoopResult
    next_context: ResearcherContext


def run_autonomous_research_cycle(
    config: AutonomousResearchCycleConfig,
) -> AutonomousResearchCycleResult:
    initial = build_researcher_context(
        config.source,
        ExperimentLedgerReader(config.ledger.path),
    )
    proposal = config.pipeline.run(initial, max_attempts=config.max_attempts)
    match proposal:
        case DroppedResearchProposal():
            raise AutonomousResearchCycleError
        case AcceptedResearchProposal():
            accepted = proposal
        case unexpected:
            assert_never(unexpected)
    artifact = accepted.strategy_artifact.artifact
    queue = load_source_driven_hypothesis_queue(accepted.queue_path)
    foundation = load_data_foundation_artifact(config.data_foundation_manifest)
    source = load_bounded_bar_source(
        config.input_csv,
        max_rows=config.max_bars,
        max_sessions=config.max_sessions,
    )
    selection = GeneratedStrategySelection(
        artifact_id=artifact.artifact_id,
        hypothesis_id=artifact.payload.hypothesis_id,
        strategy_version=f"generated-python:{artifact.artifact_id}",
        queue_card_key=artifact.payload.card_key,
        data_foundation_sha256=foundation.sha256,
        runtime_fingerprint=artifact.payload.runtime.runtime_fingerprint,
        sandbox_profile_version=artifact.payload.runtime.sandbox_profile_version,
    )
    manifest = GeneratedIntradayResearchManifest(
        hypotheses=(selection,),
        source_queue_snapshot_id=queue.snapshot_id,
        input_sha256=source.sha256,
        registered_at=accepted.proposal.llm_receipt.called_at + dt.timedelta(seconds=1),
        minimum_training_sessions=config.minimum_training_sessions,
        max_bars=config.max_bars,
        max_sessions=config.max_sessions,
        per_side_fee_bps=config.per_side_fee_bps,
        per_side_slippage_bps=config.per_side_slippage_bps,
        bootstrap_samples=config.bootstrap_samples,
        rss_limit_gib=config.rss_limit_gib,
    )
    historical = run_generated_intraday_loop(
        manifest,
        selection,
        accepted.strategy_artifact,
        config.sandbox,
        GeneratedIntradayLoopPaths(
            input_csv=config.input_csv,
            experiment_ledger=config.ledger.path,
            experiment_root=config.experiment_root,
            review_root=config.review_root,
            source_queue_artifact=accepted.queue_path,
            data_foundation_manifest=config.data_foundation_manifest,
        ),
    )
    rebuilt = build_researcher_context(
        config.source,
        ExperimentLedgerReader(config.ledger.path),
    )
    digest = rebuilt.failure_digest
    next_context = replace(
        rebuilt,
        failure_digest=FailureDigest(
            censored_reasons=digest.censored_reasons,
            failed_falsifications=digest.failed_falsifications,
            rejected_hypothesis_texts=digest.rejected_hypothesis_texts,
            reviewer_decisions=(historical.decision.value,),
        ),
    )
    return AutonomousResearchCycleResult(accepted, historical, next_context)
