from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.intraday_equal_risk_comparison_models import (
    EqualRiskComparisonStatus,
)
from trading_agent.intraday_overfit_diagnostics_models import (
    IntradayOverfitDiagnosticsStatus,
)
from trading_agent.intraday_research_loop_models import IntradayReviewerDecision

_HEX40: Final = re.compile(r"^[0-9a-f]{40}$")
_HEX40_OR_64: Final = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
_RUN_KEY: Final = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True, slots=True)
class IntradayActualResearchAuditError(ValueError):
    reason: str

    @override
    def __str__(self) -> str:
        return f"intraday actual research terminal audit blocked: {self.reason}"


class IntradayActualResearchAuditRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_key: str
    plan_path: Path
    research_receipt: Path
    research_report: Path
    expected_dataset_producer_commit_sha: str
    expected_code_version: str
    output_root: Path

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        paths = (
            self.plan_path,
            self.research_receipt,
            self.research_report,
            self.output_root,
        )
        if (
            _RUN_KEY.fullmatch(self.run_key) is None
            or not all(path.is_absolute() for path in paths)
            or _HEX40.fullmatch(self.expected_dataset_producer_commit_sha) is None
            or _HEX40_OR_64.fullmatch(self.expected_code_version) is None
        ):
            raise IntradayActualResearchAuditError("invalid_request")
        return self


class IntradayActualResearchAuditPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2, 3] = 3
    run_key: str
    plan_id: str
    research_completed_at_epoch: int
    dataset_input_sha256: str
    dataset_receipt_sha256: str
    dataset_producer_commit_sha: str
    manifest_sha256: str
    strategy_code_version: str
    foundation_sha256s: tuple[str, ...]
    trial_ids: tuple[str, ...]
    experiment_artifact_ids: tuple[str, ...]
    review_artifact_ids: tuple[str, ...]
    reviewer_decisions: tuple[IntradayReviewerDecision, ...]
    comparison_artifact_id: str | None
    comparison_status: EqualRiskComparisonStatus | None
    overfit_diagnostics_artifact_id: str | None = None
    overfit_diagnostics_status: IntradayOverfitDiagnosticsStatus | None = None
    automatic_state_change_allowed: Literal[False] = False
    order_authority_change_allowed: Literal[False] = False
    allocation_change_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        cardinality = len(self.foundation_sha256s)
        comparison_required = cardinality >= 2
        diagnostics_required = self.schema_version == 3 and cardinality == 3
        if (
            _RUN_KEY.fullmatch(self.run_key) is None
            or _HEX64.fullmatch(self.plan_id) is None
            or self.research_completed_at_epoch < 1
            or _HEX64.fullmatch(self.dataset_input_sha256) is None
            or _HEX64.fullmatch(self.dataset_receipt_sha256) is None
            or _HEX40.fullmatch(self.dataset_producer_commit_sha) is None
            or _HEX64.fullmatch(self.manifest_sha256) is None
            or _HEX40_OR_64.fullmatch(self.strategy_code_version) is None
            or not 1 <= cardinality <= 3
            or any(_HEX64.fullmatch(value) is None for value in self.foundation_sha256s)
            or any(_HEX64.fullmatch(value) is None for value in self.experiment_artifact_ids)
            or any(_HEX64.fullmatch(value) is None for value in self.review_artifact_ids)
            or (self.comparison_artifact_id is not None and _HEX64.fullmatch(self.comparison_artifact_id) is None)
            or (self.comparison_artifact_id is not None) is not comparison_required
            or (self.comparison_status is not None) is not comparison_required
            or (
                self.overfit_diagnostics_artifact_id is not None
                and _HEX64.fullmatch(
                    self.overfit_diagnostics_artifact_id
                )
                is None
            )
            or (
                self.overfit_diagnostics_artifact_id is not None
            )
            is not diagnostics_required
            or (
                self.overfit_diagnostics_status is not None
            )
            is not diagnostics_required
            or any(not value for value in self.trial_ids)
            or any(
                len(set(values)) != cardinality
                for values in (
                    self.foundation_sha256s,
                    self.trial_ids,
                    self.experiment_artifact_ids,
                    self.review_artifact_ids,
                )
            )
            or not all(
                len(values) == cardinality
                for values in (
                    self.trial_ids,
                    self.experiment_artifact_ids,
                    self.review_artifact_ids,
                    self.reviewer_decisions,
                )
            )
        ):
            raise IntradayActualResearchAuditError("invalid_audit_payload")
        return self


class IntradayActualResearchAuditArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2, 3] = 3
    artifact_id: str
    payload: IntradayActualResearchAuditPayload

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if (
            self.artifact_id != expected
            or self.schema_version != self.payload.schema_version
        ):
            raise IntradayActualResearchAuditError("invalid_artifact_id")
        return self


@dataclass(frozen=True, slots=True)
class IntradayActualResearchAuditResult:
    artifact: IntradayActualResearchAuditArtifact
    artifact_path: Path
    created: bool


__all__ = (
    "IntradayActualResearchAuditArtifact",
    "IntradayActualResearchAuditError",
    "IntradayActualResearchAuditPayload",
    "IntradayActualResearchAuditRequest",
    "IntradayActualResearchAuditResult",
)
