from __future__ import annotations

import datetime as dt
import hashlib
import re
from enum import StrEnum
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.intraday_research_loop_models import IntradayReviewerDecision

COMPARISON_MIN_SESSIONS: Final = 20
COMPARISON_MIN_TRADES: Final = 30
INTRADAY_EQUAL_RISK_COMPARISON_VERSION: Final = "intraday_equal_risk_comparison_v1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class InvalidEqualRiskComparisonError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday equal-risk comparison evidence is invalid"


class EqualRiskComparisonStatus(StrEnum):
    COLLECTING = "collecting"
    COMPARISON_READY = "comparison_ready"


class EqualRiskComparisonCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trial_id: str
    strategy_version: str
    experiment_artifact_id: str
    review_artifact_id: str
    observed_sessions: int = Field(ge=1)
    trade_count: int = Field(ge=0)
    reviewer_decision: IntradayReviewerDecision

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        if (
            _IDENTIFIER.fullmatch(self.trial_id) is None
            or _IDENTIFIER.fullmatch(self.strategy_version) is None
            or _HEX64.fullmatch(self.experiment_artifact_id) is None
            or _HEX64.fullmatch(self.review_artifact_id) is None
        ):
            raise InvalidEqualRiskComparisonError
        return self


def equal_risk_comparison_blockers(
    candidates: tuple[EqualRiskComparisonCandidate, ...],
) -> tuple[str, ...]:
    blockers: list[str] = []
    for candidate in candidates:
        if candidate.observed_sessions < COMPARISON_MIN_SESSIONS:
            blockers.append(
                "minimum_comparison_sessions:"
                f"{candidate.strategy_version}:"
                f"{candidate.observed_sessions}/{COMPARISON_MIN_SESSIONS}"
            )
        if candidate.trade_count < COMPARISON_MIN_TRADES:
            blockers.append(
                "minimum_comparison_trades:"
                f"{candidate.strategy_version}:"
                f"{candidate.trade_count}/{COMPARISON_MIN_TRADES}"
            )
    return tuple(sorted(blockers))


def equal_risk_comparison_status(
    candidates: tuple[EqualRiskComparisonCandidate, ...],
) -> EqualRiskComparisonStatus:
    if len(candidates) < 2 or equal_risk_comparison_blockers(candidates):
        return EqualRiskComparisonStatus.COLLECTING
    return EqualRiskComparisonStatus.COMPARISON_READY


class EqualRiskComparisonPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    comparison_version: Literal["intraday_equal_risk_comparison_v1"]
    reviewed_at: dt.datetime
    data_version: str
    manifest_sha256: str
    evaluator_version: str
    side_cost_bps: int = Field(ge=20, le=100)
    candidates: tuple[EqualRiskComparisonCandidate, ...]
    status: EqualRiskComparisonStatus
    blockers: tuple[str, ...]
    automatic_state_change_allowed: Literal[False] = False
    order_authority_change_allowed: Literal[False] = False
    allocation_change_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        ordered = tuple(sorted(self.candidates, key=lambda item: item.strategy_version))
        trial_ids = tuple(item.trial_id for item in self.candidates)
        strategy_versions = tuple(item.strategy_version for item in self.candidates)
        experiment_ids = tuple(item.experiment_artifact_id for item in self.candidates)
        review_ids = tuple(item.review_artifact_id for item in self.candidates)
        expected_blockers = equal_risk_comparison_blockers(self.candidates)
        if (
            not _aware(self.reviewed_at)
            or _HEX64.fullmatch(self.data_version) is None
            or _HEX64.fullmatch(self.manifest_sha256) is None
            or _IDENTIFIER.fullmatch(self.evaluator_version) is None
            or not 2 <= len(self.candidates) <= 3
            or self.candidates != ordered
            or any(
                len(set(values)) != len(values) for values in (trial_ids, strategy_versions, experiment_ids, review_ids)
            )
            or self.status is not equal_risk_comparison_status(self.candidates)
            or self.blockers != expected_blockers
        ):
            raise InvalidEqualRiskComparisonError
        return self


class EqualRiskComparisonArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str
    payload: EqualRiskComparisonPayload

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if self.artifact_id != expected:
            raise InvalidEqualRiskComparisonError
        return self


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "COMPARISON_MIN_SESSIONS",
    "COMPARISON_MIN_TRADES",
    "INTRADAY_EQUAL_RISK_COMPARISON_VERSION",
    "EqualRiskComparisonArtifact",
    "EqualRiskComparisonCandidate",
    "EqualRiskComparisonPayload",
    "EqualRiskComparisonStatus",
    "InvalidEqualRiskComparisonError",
    "equal_risk_comparison_blockers",
    "equal_risk_comparison_status",
)
