from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from trading_agent.day_historical_evidence_models import (
    DayDiscoveryEvidenceFeedback,
    DayHistoricalEvidencePayload,
    DayHistoricalEvidenceSeal,
    DayHistoricalPreregistration,
    DayHoldoutRevealReceipt,
    DayMarketCostEvaluator,
    DayPointInTimeDataManifest,
    DaySelectionDiagnostics,
    InvalidDayHistoricalEvidenceError,
    ValidatedMarketTimeSeriesEValueEvaluator,
)
from trading_agent.day_strategy_capsule_models import StrategyCapsule
from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.intraday_overfit_diagnostics import require_all_attempts_in_selection_diagnostics
from trading_agent.intraday_overfit_diagnostics_models import InvalidIntradayOverfitDiagnosticsError
from trading_agent.strategy_research_models import PreregistrationManifest
from trading_agent.strategy_research_types import AttemptStatus


@dataclass(frozen=True, slots=True)
class DayHistoricalEvidenceRequest:
    ledger: ExperimentLedgerReader
    capsule: StrategyCapsule
    preregistration: DayHistoricalPreregistration
    data_manifest: DayPointInTimeDataManifest
    cost_evaluator: DayMarketCostEvaluator
    selection_diagnostics: DaySelectionDiagnostics
    evaluated_at: dt.datetime
    next_review_date: dt.date
    artifact_refs: tuple[str, ...]
    online_e_value_or_fdr_claim: bool = False
    e_value_evaluator: ValidatedMarketTimeSeriesEValueEvaluator | None = None


@dataclass(frozen=True, slots=True)
class DayHistoricalEvidenceResult:
    seal: DayHistoricalEvidenceSeal
    feedback: DayDiscoveryEvidenceFeedback


def build_day_historical_evidence(
    request: DayHistoricalEvidenceRequest,
) -> DayHistoricalEvidenceResult:
    capsule = StrategyCapsule.model_validate(request.capsule.model_dump(mode="python"))
    stored_capsule = request.ledger.day_strategy_capsule(capsule.capsule_id)
    stored_version = request.ledger.day_hypothesis_version(capsule.hypothesis_version_id)
    if (
        stored_capsule is None
        or stored_capsule.capsule != capsule
        or stored_version is None
        or stored_version.version.market_id is not capsule.market_id
    ):
        raise InvalidDayHistoricalEvidenceError("capsule or market lineage is absent from ledger")
    version = stored_version.version
    attempts = request.ledger.day_attempts_for_review(
        version.market_id,
        version.hypothesis_version_id,
    )
    attempt_ids = tuple(sorted(item.attempt.attempt_id for item in attempts))
    try:
        require_all_attempts_in_selection_diagnostics(
            attempt_ids,
            request.selection_diagnostics.input_attempt_ids,
        )
    except InvalidIntradayOverfitDiagnosticsError:
        raise InvalidDayHistoricalEvidenceError("all ledger attempts must enter selection diagnostics") from None
    legacy_ids = {item.attempt.hypothesis_id for item in attempts}
    if len(legacy_ids) != 1 or any(item.attempt.status is AttemptStatus.STARTED for item in attempts):
        raise InvalidDayHistoricalEvidenceError("terminal attempted lineage is required")
    legacy_id = next(iter(legacy_ids))
    manifest = _manifest(request.ledger, legacy_id)
    _require_preregistered_lineage(request, manifest)
    hypothesis = manifest.hypothesis
    reveals = tuple(
        item
        for item in request.ledger.strategy_research_sanitized_reveals(hypothesis.agent_id)
        if item.sanitized_result.hypothesis_id == legacy_id
    )
    if len(reveals) != 1:
        raise InvalidDayHistoricalEvidenceError("exact lineage must have one ledger holdout reveal")
    reveal = reveals[0]
    parameter_set_sha256 = capsule.attempt_binding_id
    receipt = DayHoldoutRevealReceipt(
        reveal_id=reveal.reveal_id,
        legacy_hypothesis_id=legacy_id,
        market_id=version.market_id,
        hypothesis_version_id=version.hypothesis_version_id,
        code_sha256=version.code_sha256,
        data_manifest_sha256=version.data_manifest_sha256,
        parameter_set_sha256=parameter_set_sha256,
        sanitized_result_id=reveal.sanitized_result.result_id,
        revealed_at=reveal.revealed_at,
    )
    refs = tuple(
        sorted(
            set(request.artifact_refs)
            | {request.selection_diagnostics.diagnostics_artifact_ref}
            | set(reveal.sanitized_result.artifact_refs)
        )
    )
    payload = DayHistoricalEvidencePayload(
        capsule_id=capsule.capsule_id,
        hypothesis_version_id=version.hypothesis_version_id,
        market_id=version.market_id,
        code_sha256=version.code_sha256,
        parameter_set_sha256=parameter_set_sha256,
        preregistration=request.preregistration,
        data_manifest=request.data_manifest,
        cost_evaluator=request.cost_evaluator,
        evaluator_sha256=capsule.evaluator_sha256,
        attempted_variant_count=len(attempts),
        selection_diagnostics=request.selection_diagnostics,
        holdout_reveal=receipt,
        classification=reveal.sanitized_result.outcome,
        artifact_refs=refs,
        evaluated_at=request.evaluated_at,
        online_e_value_or_fdr_claim=request.online_e_value_or_fdr_claim,
        e_value_evaluator=request.e_value_evaluator,
    )
    seal = DayHistoricalEvidenceSeal(seal_id=payload.content_sha256, payload=payload)
    feedback = DayDiscoveryEvidenceFeedback(
        classification=payload.classification,
        reason_codes=tuple(item.value for item in reveal.sanitized_result.reason_codes),
        preregistered_summary=request.preregistration.power_or_ci_gate,
        selection_diagnostics_status=request.selection_diagnostics.status,
        next_review_date=request.next_review_date,
    )
    return DayHistoricalEvidenceResult(seal, feedback)


def _manifest(ledger: ExperimentLedgerReader, hypothesis_id: str) -> PreregistrationManifest:
    matches = tuple(
        item for item in ledger.strategy_research_preregistrations() if item.hypothesis.hypothesis_id == hypothesis_id
    )
    if len(matches) != 1:
        raise InvalidDayHistoricalEvidenceError("preregistration authority is missing")
    return matches[0]


def _require_preregistered_lineage(
    request: DayHistoricalEvidenceRequest,
    manifest: PreregistrationManifest,
) -> None:
    hypothesis = manifest.hypothesis
    version = request.ledger.day_hypothesis_version(request.capsule.hypothesis_version_id)
    if version is None:
        raise InvalidDayHistoricalEvidenceError("day hypothesis version is missing")
    day = version.version
    prereg = request.preregistration
    attempts = request.ledger.day_attempts_for_review(day.market_id, day.hypothesis_version_id)
    earliest_attempt = min(item.attempt.started_at for item in attempts)
    if (
        prereg.preregistration_sha256 != manifest.content_sha256
        or prereg.preregistered_at != manifest.preregistered_at
        or prereg.train.model_dump() != hypothesis.train_period.model_dump()
        or prereg.validation.model_dump() != hypothesis.validation_period.model_dump()
        or prereg.holdout_seal_id != hypothesis.holdout_period_sealed_ref.seal_id
        or prereg.holdout_commitment_sha256 != hypothesis.holdout_period_sealed_ref.commitment_sha256
        or prereg.preregistered_at >= earliest_attempt
        or any(prereg.content_sha256 not in item.attempt.input_hashes for item in attempts)
        or day.code_sha256 != hypothesis.code_sha256
        or day.data_manifest_sha256 != hypothesis.data_manifest_sha256
        or request.data_manifest.market_id is not day.market_id
        or request.data_manifest.data_manifest_sha256 != day.data_manifest_sha256
        or request.data_manifest.universe_snapshot_id != day.universe_snapshot_id
        or request.data_manifest.point_in_time_as_of != day.universe_snapshot_at
        or request.cost_evaluator.market_id is not day.market_id
        or request.cost_evaluator.cost_model_id != day.cost_model.model_id
        or request.cost_evaluator.slippage_model_id != request.capsule.slippage_model_id
        or request.cost_evaluator.evaluator_sha256 != request.capsule.evaluator_sha256
    ):
        raise InvalidDayHistoricalEvidenceError("preregistered market lineage does not match ledger")


__all__ = (
    "DayHistoricalEvidenceRequest",
    "DayHistoricalEvidenceResult",
    "build_day_historical_evidence",
)
