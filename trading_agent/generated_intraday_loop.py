from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import override

from trading_agent.data_capability_models import DataUse
from trading_agent.data_foundation_manifest import load_data_foundation_artifact
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.generated_intraday_registration import register_generated_intraday_strategy
from trading_agent.generated_intraday_research_models import (
    GeneratedIntradayResearchManifest,
    GeneratedStrategySelection,
)
from trading_agent.generated_intraday_trial import (
    GeneratedIntradayTrialContext,
    run_or_replay_generated_intraday_trial,
)
from trading_agent.generated_strategy_artifact import PublishedGeneratedStrategy
from trading_agent.generated_strategy_sandbox import GeneratedStrategySandbox
from trading_agent.heavy_empirical_lease import HeavyEmpiricalLeaseError, heavy_empirical_lease
from trading_agent.intraday_research_loop_models import IntradayReviewerDecision
from trading_agent.intraday_research_reviewer import IntradayReviewRequest, review_intraday_experiment
from trading_agent.replay import load_bounded_bar_source
from trading_agent.research_identity_models import AgentFamily, MarketId
from trading_agent.strategy_data_gate import StrategyDataStatus


class GeneratedIntradayLoopError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "generated intraday research loop failed closed"


@dataclass(frozen=True, slots=True)
class GeneratedIntradayLoopPaths:
    input_csv: Path
    experiment_ledger: Path
    experiment_root: Path
    review_root: Path
    source_queue_artifact: Path
    data_foundation_manifest: Path


@dataclass(frozen=True, slots=True)
class GeneratedIntradayLoopResult:
    trial_id: str
    experiment_artifact_id: str
    review_artifact_id: str
    decision: IntradayReviewerDecision
    experiment_created: bool
    review_created: bool


def run_generated_intraday_loop(
    manifest: GeneratedIntradayResearchManifest,
    selection: GeneratedStrategySelection,
    published: PublishedGeneratedStrategy,
    sandbox: GeneratedStrategySandbox,
    paths: GeneratedIntradayLoopPaths,
) -> GeneratedIntradayLoopResult:
    try:
        if manifest.hypotheses != (selection,):
            raise GeneratedIntradayLoopError
        source = load_bounded_bar_source(
            paths.input_csv,
            max_rows=manifest.max_bars,
            max_sessions=manifest.max_sessions,
        )
        if source.sha256 != manifest.input_sha256:
            raise GeneratedIntradayLoopError
        _require_foundation(manifest, selection, paths.data_foundation_manifest)
        _ = register_generated_intraday_strategy(
            ExperimentLedgerStore(paths.experiment_ledger),
            paths.source_queue_artifact,
            manifest,
            selection,
            published,
        )
        manifest_sha256 = hashlib.sha256(
            canonical_experiment_ledger_json(manifest).encode()
        ).hexdigest()
        context = GeneratedIntradayTrialContext(
            manifest=manifest,
            experiment_ledger=paths.experiment_ledger,
            artifact_root=paths.experiment_root,
            data_version=source.sha256,
            manifest_sha256=manifest_sha256,
            bars=source.bars,
            published=published,
            sandbox=sandbox,
        )
        with heavy_empirical_lease(paths.experiment_ledger):
            experiment, experiment_created = run_or_replay_generated_intraday_trial(
                context,
                selection,
            )
            review, review_created = review_intraday_experiment(
                IntradayReviewRequest(
                    ledger=ExperimentLedgerReader(paths.experiment_ledger),
                    experiment=experiment,
                    review_root=paths.review_root,
                    reviewed_at=manifest.registered_at + dt.timedelta(seconds=4),
                )
            )
        return GeneratedIntradayLoopResult(
            trial_id=experiment.payload.trial_id,
            experiment_artifact_id=experiment.artifact_id,
            review_artifact_id=review.artifact_id,
            decision=review.payload.decision,
            experiment_created=experiment_created,
            review_created=review_created,
        )
    except GeneratedIntradayLoopError:
        raise
    except (HeavyEmpiricalLeaseError, OSError, TypeError, ValueError):
        raise GeneratedIntradayLoopError from None


def _require_foundation(
    manifest: GeneratedIntradayResearchManifest,
    selection: GeneratedStrategySelection,
    path: Path,
) -> None:
    artifact = load_data_foundation_artifact(path)
    foundation = artifact.manifest
    lane = foundation.strategy_lane
    decision = foundation.evaluate_data_readiness()
    if (
        artifact.sha256 != selection.data_foundation_sha256
        or lane.market_id is not MarketId.US_EQUITIES
        or lane.agent_family is not AgentFamily.DAY_TRADING
        or foundation.evaluated_at > manifest.registered_at
        or decision.status is not StrategyDataStatus.READY
        or any(
            requirement.data_use is not DataUse.HISTORICAL_RESEARCH
            or requirement.event_type != "minute_bar"
            for requirement in foundation.requirements
        )
    ):
        raise GeneratedIntradayLoopError
