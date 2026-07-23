from __future__ import annotations

import datetime as dt
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.experiment_ledger_models import TrialKind
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.intraday_equal_risk_comparison import (
    EqualRiskComparisonRequest,
    build_equal_risk_comparison_payload,
)
from trading_agent.intraday_equal_risk_comparison_models import (
    InvalidEqualRiskComparisonError,
)
from trading_agent.intraday_overfit_diagnostics_models import (
    INTRADAY_OVERFIT_DIAGNOSTICS_VERSION,
    IntradayOverfitCandidateTrace,
    IntradayOverfitDiagnosticsArtifact,
    IntradayOverfitDiagnosticsPayload,
    InvalidIntradayOverfitDiagnosticsError,
    calculate_intraday_overfit_statistics,
)
from trading_agent.intraday_research_artifacts import IntradayExperimentArtifact
from trading_agent.intraday_research_reviewer import IntradayReviewArtifact
from trading_agent.lane_policy_models import LaneId
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)


@dataclass(frozen=True, slots=True)
class IntradayOverfitDiagnosticsRequest:
    ledger: ExperimentLedgerReader
    experiments: tuple[IntradayExperimentArtifact, ...]
    reviews: tuple[IntradayReviewArtifact, ...]
    artifact_root: Path
    reviewed_at: dt.datetime


def diagnose_intraday_overfit(
    request: IntradayOverfitDiagnosticsRequest,
) -> tuple[IntradayOverfitDiagnosticsArtifact, bool]:
    try:
        payload = _diagnostics_payload(request)
        artifact = IntradayOverfitDiagnosticsArtifact(
            artifact_id=hashlib.sha256(
                canonical_experiment_ledger_json(payload).encode()
            ).hexdigest(),
            payload=payload,
        )
        created = publish_private_immutable_text(
            request.artifact_root
            / f"intraday_overfit_diagnostics_{artifact.artifact_id}.json",
            canonical_experiment_ledger_json(artifact) + "\n",
        )
        return artifact, created
    except InvalidIntradayOverfitDiagnosticsError:
        raise
    except (
        InvalidEqualRiskComparisonError,
        InvalidExperimentLedgerSourceError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidIntradayOverfitDiagnosticsError from None


def load_intraday_overfit_diagnostics_artifact(
    path: Path,
) -> IntradayOverfitDiagnosticsArtifact:
    try:
        payload = read_private_text(path)
        artifact = IntradayOverfitDiagnosticsArtifact.model_validate_json(
            payload
        )
        expected_name = (
            f"intraday_overfit_diagnostics_{artifact.artifact_id}.json"
        )
        if (
            path.name != expected_name
            or payload
            != canonical_experiment_ledger_json(artifact) + "\n"
        ):
            raise InvalidIntradayOverfitDiagnosticsError
        return artifact
    except InvalidIntradayOverfitDiagnosticsError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidIntradayOverfitDiagnosticsError from None


def _diagnostics_payload(
    request: IntradayOverfitDiagnosticsRequest,
) -> IntradayOverfitDiagnosticsPayload:
    comparison = build_equal_risk_comparison_payload(
        EqualRiskComparisonRequest(
            ledger=request.ledger,
            experiments=request.experiments,
            reviews=request.reviews,
            artifact_root=request.artifact_root,
            reviewed_at=request.reviewed_at,
        )
    )
    experiments = {
        item.payload.strategy_version: item
        for item in request.experiments
    }
    candidates: list[IntradayOverfitCandidateTrace] = []
    for candidate in comparison.candidates:
        experiment = experiments.get(candidate.strategy_version)
        if experiment is None:
            raise InvalidIntradayOverfitDiagnosticsError
        result = experiment.payload.result
        if result.schema_version == 1:
            dates: tuple[dt.date, ...] = ()
            returns: tuple[float, ...] = ()
        else:
            dates = tuple(item.session_date for item in result.session_outcomes)
            returns = tuple(
                _compound_return(item.net_trade_returns)
                for item in result.session_outcomes
            )
        candidates.append(
            IntradayOverfitCandidateTrace(
                trace_schema_version=result.schema_version,
                trial_id=candidate.trial_id,
                strategy_version=candidate.strategy_version,
                experiment_artifact_id=candidate.experiment_artifact_id,
                review_artifact_id=candidate.review_artifact_id,
                trade_count=candidate.trade_count,
                session_dates=dates,
                net_session_returns=returns,
            )
        )
    ordered = tuple(sorted(candidates, key=lambda item: item.strategy_version))
    trial_count = sum(
        stored.registration.trial_kind is TrialKind.HISTORICAL_REPLAY
        and stored.registration.experiment_scope.primary_lane
        is LaneId.INTRADAY_MOMENTUM
        and stored.registration.registered_at <= request.reviewed_at
        for stored in request.ledger.trials()
    )
    statistics = calculate_intraday_overfit_statistics(
        ordered,
        total_lane_historical_trials=trial_count,
    )
    return IntradayOverfitDiagnosticsPayload(
        diagnostics_version=INTRADAY_OVERFIT_DIAGNOSTICS_VERSION,
        reviewed_at=request.reviewed_at,
        data_version=comparison.data_version,
        manifest_sha256=comparison.manifest_sha256,
        evaluator_version=comparison.evaluator_version,
        side_cost_bps=comparison.side_cost_bps,
        statistics=statistics,
    )


def _compound_return(values: tuple[float, ...]) -> float:
    return math.prod(1.0 + value for value in values) - 1.0


__all__ = (
    "IntradayOverfitDiagnosticsRequest",
    "diagnose_intraday_overfit",
    "load_intraday_overfit_diagnostics_artifact",
)
