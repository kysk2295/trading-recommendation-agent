from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, override

from trading_agent.daily_research_contract import strategy_contract
from trading_agent.experiment_ledger_bootstrap import bootstrap_current_intraday_experiments
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import ExperimentLedgerReader, ExperimentLedgerStore
from trading_agent.heavy_empirical_lease import HeavyEmpiricalLeaseError, heavy_empirical_lease
from trading_agent.intraday_research_data_gate import require_intraday_research_data
from trading_agent.intraday_research_loop_models import IntradayResearchManifest, IntradayReviewerDecision
from trading_agent.intraday_research_reviewer import IntradayReviewRequest, review_intraday_experiment
from trading_agent.intraday_research_trial import IntradayTrialExecutionContext, run_or_replay_intraday_trial
from trading_agent.lane_identity_models import LaneId
from trading_agent.lane_registry_store import LaneRegistryReader
from trading_agent.replay import load_bounded_bar_source
from trading_agent.source_backed_intraday_design import register_source_backed_intraday_design
from trading_agent.source_driven_hypothesis_queue import load_source_driven_hypothesis_queue

_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")


class IntradayResearchLoopError(RuntimeError):
    @override
    def __str__(self) -> str:
        return "bounded intraday research and review loop failed"


@dataclass(frozen=True, slots=True)
class IntradayResearchLoopPaths:
    input_csv: Path
    lane_registry: Path
    experiment_ledger: Path
    artifact_root: Path
    review_root: Path
    source_queue_artifact: Path | None = None
    data_foundation_manifests: tuple[Path, ...] = ()
    persisted_manifest_sha256: str | None = None
    required_outcome_trace_schema_version: Literal[2] | None = None


@dataclass(frozen=True, slots=True)
class IntradayResearchLoopResult:
    trials_total: int
    experiment_artifacts_created: int
    review_artifacts_created: int
    decisions: tuple[IntradayReviewerDecision, ...]


def run_intraday_research_loop(
    manifest: IntradayResearchManifest,
    paths: IntradayResearchLoopPaths,
) -> IntradayResearchLoopResult:
    if manifest.schema_version == 1 and any(
        strategy_contract(item.strategy).hypothesis_id != item.hypothesis_id for item in manifest.hypotheses
    ):
        raise IntradayResearchLoopError
    lane_manifests = tuple(
        stored.manifest
        for stored in LaneRegistryReader(paths.lane_registry).manifests()
        if stored.manifest.lane_id is LaneId.INTRADAY_MOMENTUM
    )
    if len(lane_manifests) != 1 or any(
        item.strategy.value not in lane_manifests[0].strategy_ids for item in manifest.hypotheses
    ):
        raise IntradayResearchLoopError
    source = load_bounded_bar_source(
        paths.input_csv,
        max_rows=manifest.max_bars,
        max_sessions=manifest.max_sessions,
    )
    if manifest.schema_version == 2 and manifest.input_sha256 != source.sha256:
        raise IntradayResearchLoopError
    require_intraday_research_data(manifest, paths.data_foundation_manifests)
    bars = source.bars
    data_version = source.sha256
    canonical_manifest_sha256 = hashlib.sha256(
        canonical_experiment_ledger_json(manifest).encode()
    ).hexdigest()
    manifest_sha256 = paths.persisted_manifest_sha256 or canonical_manifest_sha256
    if _HEX64.fullmatch(manifest_sha256) is None:
        raise IntradayResearchLoopError
    if manifest.schema_version == 1:
        _ = bootstrap_current_intraday_experiments(
            lane_registry=LaneRegistryReader(paths.lane_registry),
            experiment_ledger=ExperimentLedgerStore(paths.experiment_ledger),
            code_version=manifest.code_version,
            recorded_at=manifest.registered_at,
        )
    else:
        if paths.source_queue_artifact is None:
            raise IntradayResearchLoopError
        queue = load_source_driven_hypothesis_queue(paths.source_queue_artifact)
        _ = register_source_backed_intraday_design(
            manifest,
            queue,
            ExperimentLedgerStore(paths.experiment_ledger),
        )
    context = IntradayTrialExecutionContext(
        manifest=manifest,
        experiment_ledger=paths.experiment_ledger,
        artifact_root=paths.artifact_root,
        data_version=data_version,
        manifest_sha256=manifest_sha256,
        bars=bars,
    )
    experiment_created = 0
    review_created = 0
    decisions: list[IntradayReviewerDecision] = []
    with _heavy_empirical_lease(paths.experiment_ledger):
        for selection in manifest.hypotheses:
            experiment, created = run_or_replay_intraday_trial(context, selection)
            if (
                paths.required_outcome_trace_schema_version is not None
                and experiment.schema_version
                != paths.required_outcome_trace_schema_version
            ):
                raise IntradayResearchLoopError
            experiment_created += int(created)
            review, created = review_intraday_experiment(
                IntradayReviewRequest(
                    ledger=ExperimentLedgerReader(paths.experiment_ledger),
                    experiment=experiment,
                    review_root=paths.review_root,
                    reviewed_at=manifest.registered_at + dt.timedelta(seconds=4),
                )
            )
            review_created += int(created)
            decisions.append(review.payload.decision)
    return IntradayResearchLoopResult(
        trials_total=len(manifest.hypotheses),
        experiment_artifacts_created=experiment_created,
        review_artifacts_created=review_created,
        decisions=tuple(decisions),
    )


@contextmanager
def _heavy_empirical_lease(ledger_path: Path) -> Iterator[None]:
    try:
        with heavy_empirical_lease(ledger_path):
            yield
    except HeavyEmpiricalLeaseError:
        raise IntradayResearchLoopError from None


__all__ = (
    "IntradayResearchLoopError",
    "IntradayResearchLoopPaths",
    "IntradayResearchLoopResult",
    "run_intraday_research_loop",
)
