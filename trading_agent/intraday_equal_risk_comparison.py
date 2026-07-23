from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.daily_research_contract import (
    CURRENT_COST_MODEL,
    CURRENT_DATA_CONTRACT,
    SHADOW_PORTFOLIO_POLICY,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.intraday_equal_risk_comparison_models import (
    INTRADAY_EQUAL_RISK_COMPARISON_VERSION,
    EqualRiskComparisonArtifact,
    EqualRiskComparisonCandidate,
    EqualRiskComparisonPayload,
    InvalidEqualRiskComparisonError,
    equal_risk_comparison_blockers,
    equal_risk_comparison_status,
)
from trading_agent.intraday_research_artifacts import IntradayExperimentArtifact
from trading_agent.intraday_research_reviewer import (
    IntradayReviewArtifact,
    InvalidIntradayResearchReviewError,
    evaluate_intraday_experiment,
)
from trading_agent.lane_policy_models import LaneId
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)


@dataclass(frozen=True, slots=True)
class EqualRiskComparisonRequest:
    ledger: ExperimentLedgerReader
    experiments: tuple[IntradayExperimentArtifact, ...]
    reviews: tuple[IntradayReviewArtifact, ...]
    artifact_root: Path
    reviewed_at: dt.datetime


def compare_intraday_equal_risk_trials(
    request: EqualRiskComparisonRequest,
) -> tuple[EqualRiskComparisonArtifact, bool]:
    try:
        payload = build_equal_risk_comparison_payload(request)
        artifact = EqualRiskComparisonArtifact(
            artifact_id=hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest(),
            payload=payload,
        )
        created = publish_private_immutable_text(
            request.artifact_root / f"intraday_equal_risk_comparison_{artifact.artifact_id}.json",
            canonical_experiment_ledger_json(artifact) + "\n",
        )
        return artifact, created
    except InvalidEqualRiskComparisonError:
        raise
    except (
        InvalidExperimentLedgerSourceError,
        InvalidIntradayResearchReviewError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidEqualRiskComparisonError from None


def build_equal_risk_comparison_payload(
    request: EqualRiskComparisonRequest,
) -> EqualRiskComparisonPayload:
    if not 2 <= len(request.experiments) <= 3 or len(request.reviews) != len(request.experiments):
        raise InvalidEqualRiskComparisonError
    reviews = {review.payload.trial_id: review for review in request.reviews}
    if len(reviews) != len(request.reviews):
        raise InvalidEqualRiskComparisonError
    versions = {
        stored.registration.strategy_version: stored.registration for stored in request.ledger.strategy_versions()
    }
    candidates: list[EqualRiskComparisonCandidate] = []
    common: tuple[str, str, str, int, int, int] | None = None
    for experiment in request.experiments:
        experiment_payload = experiment.payload
        review = reviews.get(experiment_payload.trial_id)
        version = versions.get(experiment_payload.strategy_version)
        if review is None or version is None:
            raise InvalidEqualRiskComparisonError
        recomputed = evaluate_intraday_experiment(
            request.ledger,
            experiment,
            review.payload.reviewed_at,
        )
        result = experiment_payload.result
        design = (
            experiment_payload.data_version,
            experiment_payload.manifest_sha256,
            experiment_payload.evaluator_version,
            result.side_cost_bps,
            result.observed_sessions,
            result.fold_count,
        )
        if (
            recomputed != review.payload
            or review.payload.strategy_version != experiment_payload.strategy_version
            or review.payload.experiment_artifact_id != experiment.artifact_id
            or review.payload.reviewed_at > request.reviewed_at
            or experiment_payload.completed_at > request.reviewed_at
            or version.strategy_id != result.strategy.value
            or version.lane_id is not LaneId.INTRADAY_MOMENTUM
            or version.data_contract != CURRENT_DATA_CONTRACT
            or version.cost_model != CURRENT_COST_MODEL
            or version.portfolio_policy != SHADOW_PORTFOLIO_POLICY
            or (common is not None and design != common)
        ):
            raise InvalidEqualRiskComparisonError
        common = design
        candidates.append(
            EqualRiskComparisonCandidate(
                trial_id=experiment_payload.trial_id,
                strategy_version=experiment_payload.strategy_version,
                experiment_artifact_id=experiment.artifact_id,
                review_artifact_id=review.artifact_id,
                observed_sessions=result.observed_sessions,
                trade_count=result.trade_count,
                reviewer_decision=review.payload.decision,
            )
        )
    if common is None or set(reviews) != {experiment.payload.trial_id for experiment in request.experiments}:
        raise InvalidEqualRiskComparisonError
    ordered = tuple(sorted(candidates, key=lambda item: item.strategy_version))
    return EqualRiskComparisonPayload(
        comparison_version=INTRADAY_EQUAL_RISK_COMPARISON_VERSION,
        reviewed_at=request.reviewed_at,
        data_version=common[0],
        manifest_sha256=common[1],
        evaluator_version=common[2],
        side_cost_bps=common[3],
        candidates=ordered,
        status=equal_risk_comparison_status(ordered),
        blockers=equal_risk_comparison_blockers(ordered),
    )


__all__ = (
    "EqualRiskComparisonRequest",
    "build_equal_risk_comparison_payload",
    "compare_intraday_equal_risk_trials",
)
