from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Literal, Self, assert_never

from pydantic import Field, model_validator

from trading_agent.intraday_overfit_diagnostics_models import IntradayOverfitDiagnosticsStatus
from trading_agent.research_identity_models import MarketId
from trading_agent.strategy_research_policy import require_validated_online_error_control
from trading_agent.strategy_research_types import (
    CanonicalModel,
    EvidenceKind,
    EvidenceUse,
    TerminalOutcome,
    aware,
)

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAFE_ARTIFACT_REF = re.compile(r"^artifact://safe/[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class InvalidDayHistoricalEvidenceError(ValueError):
    reason: str = "day historical evidence is invalid"

    def __str__(self) -> str:
        return self.reason


class DayEvidenceWindow(CanonicalModel):
    start: dt.datetime
    end: dt.datetime

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if not aware(self.start) or not aware(self.end) or self.end <= self.start:
            raise InvalidDayHistoricalEvidenceError("historical evidence window is invalid")
        return self


class DayHistoricalPreregistration(CanonicalModel):
    preregistration_sha256: str = Field(pattern=_SHA256_PATTERN)
    holdout_seal_id: str = Field(min_length=1)
    holdout_commitment_sha256: str = Field(pattern=_SHA256_PATTERN)
    preregistered_at: dt.datetime
    train: DayEvidenceWindow
    validation: DayEvidenceWindow
    sealed_holdout: DayEvidenceWindow
    purge: dt.timedelta = Field(gt=dt.timedelta(0))
    embargo: dt.timedelta = Field(gt=dt.timedelta(0))
    power_or_ci_gate: str = Field(min_length=1, max_length=280)

    @model_validator(mode="after")
    def validate_preregistration(self) -> Self:
        if (
            not aware(self.preregistered_at)
            or self.train.end + self.purge > self.validation.start
            or self.validation.end + self.embargo > self.sealed_holdout.start
        ):
            raise InvalidDayHistoricalEvidenceError("preregistered purge or embargo window is invalid")
        return self


class DayPointInTimeDataManifest(CanonicalModel):
    market_id: MarketId
    data_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    universe_snapshot_id: str = Field(min_length=1, max_length=160)
    point_in_time_as_of: dt.datetime
    source_kind: EvidenceKind
    evidence_use: EvidenceUse
    full_universe: Literal[False] = False

    @model_validator(mode="after")
    def validate_evidence_use(self) -> Self:
        if not aware(self.point_in_time_as_of):
            raise InvalidDayHistoricalEvidenceError("point-in-time data timestamp is invalid")
        match self.source_kind:
            case EvidenceKind.REAL:
                if self.evidence_use is not EvidenceUse.RESEARCH:
                    raise InvalidDayHistoricalEvidenceError("real data must be research evidence")
            case EvidenceKind.FIXTURE | EvidenceKind.SYNTHETIC | EvidenceKind.REPLAY | EvidenceKind.BACKTEST:
                if self.evidence_use is not EvidenceUse.WIRING_ONLY:
                    raise InvalidDayHistoricalEvidenceError("synthetic or replay data must be wiring-only")
            case unreachable:
                assert_never(unreachable)
        return self


class DayMarketCostEvaluator(CanonicalModel):
    market_id: MarketId
    cost_model_id: str = Field(min_length=1, max_length=160)
    slippage_model_id: str = Field(min_length=1, max_length=160)
    evaluator_sha256: str = Field(pattern=_SHA256_PATTERN)


class DaySelectionDiagnostics(CanonicalModel):
    market_id: MarketId
    input_attempt_ids: tuple[str, ...] = Field(min_length=1, max_length=10_000)
    total_attempted_variants: int = Field(ge=1, le=10_000)
    status: IntradayOverfitDiagnosticsStatus
    diagnostics_artifact_ref: str
    diagnostics_sha256: str = Field(pattern=_SHA256_PATTERN)
    deflated_sharpe_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    pbo_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    cscv_partitions: int | None = Field(default=None, ge=4, le=16)

    @model_validator(mode="after")
    def validate_attempt_inputs(self) -> Self:
        metrics = (
            self.deflated_sharpe_probability,
            self.pbo_probability,
            self.cscv_partitions,
        )
        if (
            self.input_attempt_ids != tuple(sorted(set(self.input_attempt_ids)))
            or any(not value.strip() for value in self.input_attempt_ids)
            or self.total_attempted_variants != len(self.input_attempt_ids)
            or self.diagnostics_artifact_ref != f"artifact://safe/{self.diagnostics_sha256}"
        ):
            raise InvalidDayHistoricalEvidenceError("all attempted variants must enter diagnostics")
        match self.status:
            case IntradayOverfitDiagnosticsStatus.COLLECTING:
                if any(value is not None for value in metrics):
                    raise InvalidDayHistoricalEvidenceError("collecting diagnostics cannot publish selection metrics")
            case IntradayOverfitDiagnosticsStatus.DIAGNOSTIC_READY:
                if any(value is None for value in metrics):
                    raise InvalidDayHistoricalEvidenceError("ready diagnostics require DSR PBO and CSCV")
            case unreachable:
                assert_never(unreachable)
        return self


class DayHoldoutRevealReceipt(CanonicalModel):
    reveal_id: str = Field(min_length=1)
    legacy_hypothesis_id: str = Field(min_length=1)
    market_id: MarketId
    hypothesis_version_id: str = Field(pattern=_SHA256_PATTERN)
    code_sha256: str = Field(pattern=_SHA256_PATTERN)
    data_manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    parameter_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    sanitized_result_id: str = Field(min_length=1)
    revealed_at: dt.datetime

    @model_validator(mode="after")
    def validate_time(self) -> Self:
        if not aware(self.revealed_at):
            raise InvalidDayHistoricalEvidenceError("holdout reveal time is invalid")
        return self


class ValidatedMarketTimeSeriesEValueEvaluator(CanonicalModel):
    version: str = Field(min_length=1, max_length=160)
    validation_artifact_ref: str
    validation_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_ref(self) -> Self:
        if self.validation_artifact_ref != f"artifact://safe/{self.validation_sha256}":
            raise InvalidDayHistoricalEvidenceError("e-value evaluator validation reference is invalid")
        return self


class DayHistoricalEvidencePayload(CanonicalModel):
    capsule_id: str = Field(pattern=_SHA256_PATTERN)
    hypothesis_version_id: str = Field(pattern=_SHA256_PATTERN)
    market_id: MarketId
    code_sha256: str = Field(pattern=_SHA256_PATTERN)
    parameter_set_sha256: str = Field(pattern=_SHA256_PATTERN)
    preregistration: DayHistoricalPreregistration
    data_manifest: DayPointInTimeDataManifest
    cost_evaluator: DayMarketCostEvaluator
    evaluator_sha256: str = Field(pattern=_SHA256_PATTERN)
    attempted_variant_count: int = Field(ge=1, le=10_000)
    selection_diagnostics: DaySelectionDiagnostics
    holdout_reveal: DayHoldoutRevealReceipt
    classification: TerminalOutcome
    artifact_refs: tuple[str, ...] = Field(min_length=1)
    evaluated_at: dt.datetime
    online_e_value_or_fdr_claim: bool = False
    e_value_evaluator: ValidatedMarketTimeSeriesEValueEvaluator | None = None
    promotion_authority: Literal[False] = False
    paper_order_authority: Literal[False] = False
    profitability_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        reveal = self.holdout_reveal
        if (
            not aware(self.evaluated_at)
            or self.data_manifest.market_id is not self.market_id
            or self.cost_evaluator.market_id is not self.market_id
            or self.selection_diagnostics.market_id is not self.market_id
            or reveal.market_id is not self.market_id
            or self.attempted_variant_count != self.selection_diagnostics.total_attempted_variants
            or reveal.hypothesis_version_id != self.hypothesis_version_id
            or reveal.code_sha256 != self.code_sha256
            or reveal.data_manifest_sha256 != self.data_manifest.data_manifest_sha256
            or reveal.parameter_set_sha256 != self.parameter_set_sha256
            or reveal.revealed_at > self.evaluated_at
        ):
            raise InvalidDayHistoricalEvidenceError("historical evidence market or lineage mismatch")
        if self.artifact_refs != tuple(sorted(set(self.artifact_refs))) or any(
            _SAFE_ARTIFACT_REF.fullmatch(value) is None for value in self.artifact_refs
        ):
            raise InvalidDayHistoricalEvidenceError("historical evidence artifact references are invalid")
        evaluator = self.e_value_evaluator
        require_validated_online_error_control(
            claimed=self.online_e_value_or_fdr_claim,
            evaluator_version=None if evaluator is None else evaluator.version,
            validation_artifact_ref=None if evaluator is None else evaluator.validation_artifact_ref,
        )
        return self


class DayHistoricalEvidenceSeal(CanonicalModel):
    seal_id: str = Field(pattern=_SHA256_PATTERN)
    payload: DayHistoricalEvidencePayload

    @model_validator(mode="after")
    def validate_seal(self) -> Self:
        if self.seal_id != self.payload.content_sha256:
            raise InvalidDayHistoricalEvidenceError("historical evidence seal identity mismatch")
        return self


class DayDiscoveryEvidenceFeedback(CanonicalModel):
    classification: TerminalOutcome
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=8)
    preregistered_summary: str = Field(min_length=1, max_length=280)
    selection_diagnostics_status: IntradayOverfitDiagnosticsStatus
    next_review_date: dt.date


__all__ = (
    "DayDiscoveryEvidenceFeedback",
    "DayEvidenceWindow",
    "DayHistoricalEvidencePayload",
    "DayHistoricalEvidenceSeal",
    "DayHistoricalPreregistration",
    "DayHoldoutRevealReceipt",
    "DayMarketCostEvaluator",
    "DayPointInTimeDataManifest",
    "DaySelectionDiagnostics",
    "InvalidDayHistoricalEvidenceError",
    "ValidatedMarketTimeSeriesEValueEvaluator",
)
