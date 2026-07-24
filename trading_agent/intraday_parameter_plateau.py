from __future__ import annotations

import datetime as dt
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from trading_agent.challenger_replay_runner import (
    run_intraday_walk_forward,
)
from trading_agent.experiment_ledger_keys import (
    canonical_experiment_ledger_json,
)
from trading_agent.experiment_ledger_store import (
    ExperimentLedgerReader,
    InvalidExperimentLedgerSourceError,
)
from trading_agent.intraday_parameter_plateau_artifacts import (
    INTRADAY_PARAMETER_PLATEAU_VERSION,
    IntradayParameterPlateauArtifact,
    IntradayParameterPlateauPayload,
    aggregate_parameter_plateau_status,
)
from trading_agent.intraday_parameter_plateau_models import (
    IntradayParameterPlateauAnalysis,
    IntradayParameterPlateauAnalysisRequest,
    IntradayParameterPlateauVariantTrace,
    InvalidIntradayParameterPlateauError,
    calculate_intraday_parameter_plateau_analysis,
)
from trading_agent.intraday_parameter_plateau_variants import (
    IntradayParameterVariant,
    parameter_variants,
)
from trading_agent.intraday_research_artifacts import (
    IntradayExperimentArtifact,
)
from trading_agent.intraday_research_loop import (
    IntradayResearchLoopError,
    _heavy_empirical_lease,
)
from trading_agent.intraday_research_loop_models import (
    IntradayResearchManifest,
    IntradayWalkForwardError,
    IntradayWalkForwardRequest,
)
from trading_agent.intraday_walk_forward_models import (
    IntradayWalkForwardResult,
)
from trading_agent.models import BarInput
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)


@dataclass(frozen=True, slots=True)
class IntradayParameterPlateauRequest:
    ledger: ExperimentLedgerReader
    manifest: IntradayResearchManifest
    bars: tuple[BarInput, ...]
    experiments: tuple[IntradayExperimentArtifact, ...]
    artifact_root: Path
    reviewed_at: dt.datetime


def diagnose_intraday_parameter_plateau(
    request: IntradayParameterPlateauRequest,
) -> tuple[IntradayParameterPlateauArtifact, bool]:
    try:
        payload = _plateau_payload(request)
        artifact = IntradayParameterPlateauArtifact(
            artifact_id=hashlib.sha256(
                canonical_experiment_ledger_json(payload).encode()
            ).hexdigest(),
            payload=payload,
        )
        created = publish_private_immutable_text(
            request.artifact_root
            / f"intraday_parameter_plateau_{artifact.artifact_id}.json",
            canonical_experiment_ledger_json(artifact) + "\n",
        )
        return artifact, created
    except InvalidIntradayParameterPlateauError:
        raise
    except (
        IntradayResearchLoopError,
        IntradayWalkForwardError,
        InvalidExperimentLedgerSourceError,
        InvalidPrivateImmutableFileError,
        OSError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidIntradayParameterPlateauError from None


def _plateau_payload(
    request: IntradayParameterPlateauRequest,
) -> IntradayParameterPlateauPayload:
    manifest = request.manifest
    experiments = {
        experiment.payload.strategy_version: experiment
        for experiment in request.experiments
    }
    if (
        manifest.schema_version != 2
        or manifest.evaluator_version != "intraday_walk_forward_v2"
        or len(experiments) != len(manifest.hypotheses)
    ):
        raise InvalidIntradayParameterPlateauError
    analyses: list[IntradayParameterPlateauAnalysis] = []
    with (
        _heavy_empirical_lease(request.ledger.path),
        tempfile.TemporaryDirectory(prefix="m8-plateau-") as temporary,
    ):
        work_root = Path(temporary)
        for selection in manifest.hypotheses:
            strategy_version = selection.strategy_version
            if strategy_version is None:
                raise InvalidIntradayParameterPlateauError
            experiment = experiments.get(strategy_version)
            registrations = tuple(
                stored.registration
                for stored in request.ledger.strategy_versions()
                if stored.registration.strategy_version
                == strategy_version
            )
            if experiment is None or len(registrations) != 1:
                raise InvalidIntradayParameterPlateauError
            variants = parameter_variants(selection.strategy)
            traces = [
                _variant_trace(
                    variants[0],
                    experiment.payload.result,
                )
            ]
            for index, variant in enumerate(variants[1:], start=1):
                result = run_intraday_walk_forward(
                    IntradayWalkForwardRequest(
                        bars=request.bars,
                        strategy=selection.strategy,
                        minimum_training_sessions=(
                            manifest.minimum_training_sessions
                        ),
                        per_side_cost_bps=(
                            manifest.per_side_total_cost_bps
                        ),
                        bootstrap_samples=manifest.bootstrap_samples,
                        rss_limit_gib=manifest.rss_limit_gib,
                        evaluator_version=manifest.evaluator_version,
                        parameter_variant=variant,
                    ),
                    work_root
                    / f"{selection.strategy.value}-{index}",
                )
                traces.append(_variant_trace(variant, result))
            analyses.append(
                calculate_intraday_parameter_plateau_analysis(
                    IntradayParameterPlateauAnalysisRequest(
                        strategy=selection.strategy,
                        trial_id=experiment.payload.trial_id,
                        strategy_version=strategy_version,
                        experiment_artifact_id=experiment.artifact_id,
                        registered_parameter_set=(
                            registrations[0].parameter_set
                        ),
                        variants=tuple(traces),
                    )
                )
            )
    ordered = tuple(
        sorted(analyses, key=lambda analysis: analysis.strategy_version)
    )
    first = request.experiments[0].payload
    if any(
        experiment.payload.data_version != first.data_version
        or experiment.payload.manifest_sha256 != first.manifest_sha256
        or experiment.payload.result.side_cost_bps
        != first.result.side_cost_bps
        for experiment in request.experiments[1:]
    ):
        raise InvalidIntradayParameterPlateauError
    return IntradayParameterPlateauPayload(
        evaluator_version=INTRADAY_PARAMETER_PLATEAU_VERSION,
        reviewed_at=request.reviewed_at,
        data_version=first.data_version,
        manifest_sha256=first.manifest_sha256,
        side_cost_bps=first.result.side_cost_bps,
        status=aggregate_parameter_plateau_status(ordered),
        analyses=ordered,
    )


def _variant_trace(
    variant: IntradayParameterVariant,
    result: IntradayWalkForwardResult,
) -> IntradayParameterPlateauVariantTrace:
    if result.schema_version != 2:
        raise InvalidIntradayParameterPlateauError
    return IntradayParameterPlateauVariantTrace(
        variant_id=variant.variant_id,
        parameter_set=variant.parameter_set,
        is_center=variant.is_center,
        session_dates=tuple(
            outcome.session_date
            for outcome in result.session_outcomes
        ),
        net_trade_returns_by_session=tuple(
            outcome.net_trade_returns
            for outcome in result.session_outcomes
        ),
        trade_count=result.trade_count,
        average_return=result.average_return,
    )


__all__ = (
    "IntradayParameterPlateauRequest",
    "diagnose_intraday_parameter_plateau",
)
