from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from typing import Literal, Self, assert_never

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_models import TrialEventKind
from trading_agent.us_news_catalyst_trial_models import (
    _HEX64,
    _SYMBOL,
    InvalidUsNewsCatalystTrialModelError,
    _aware,
    _canonical_set,
    _payload_id,
)

US_NEWS_CATALYST_EVALUATOR_VERSION = "us_news_setup_confirmation_v1"
US_NEWS_CATALYST_SETUP_HORIZON = dt.timedelta(minutes=30)


class UsNewsCatalystSetupFeatureObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    feature_evidence_id: str
    observed_at: dt.datetime
    close: Decimal
    vwap: Decimal
    rvol: Decimal
    breakout_close_above_prior_high: bool

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if (
            _SYMBOL.fullmatch(self.symbol) is None
            or _HEX64.fullmatch(self.feature_evidence_id) is None
            or not _aware(self.observed_at)
            or not _positive(self.close)
            or not _positive(self.vwap)
            or not _positive(self.rvol)
        ):
            raise InvalidUsNewsCatalystTrialModelError
        return self

    @property
    def setup_confirmed(self) -> bool:
        return (
            self.close > self.vwap
            and self.rvol >= Decimal("1.5")
            and self.breakout_close_above_prior_high
        )


class UsNewsCatalystSetupObservationManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    manifest_id: str
    trial_id: str
    cohort_artifact_id: str
    evaluator_version: str
    observations: tuple[UsNewsCatalystSetupFeatureObservation, ...] = Field(
        min_length=1,
        max_length=40,
    )

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        symbols = tuple(item.symbol for item in self.observations)
        if (
            self.manifest_id != _manifest_id(self)
            or not self.trial_id
            or _HEX64.fullmatch(self.cohort_artifact_id) is None
            or self.evaluator_version != US_NEWS_CATALYST_EVALUATOR_VERSION
            or symbols != tuple(sorted(set(symbols)))
        ):
            raise InvalidUsNewsCatalystTrialModelError
        return self


class UsNewsCatalystTrialOutcomePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    trial_id: str
    strategy_version: str
    session_date: dt.date
    cohort_artifact_id: str
    observation_manifest_id: str | None
    terminal_kind: TrialEventKind
    reason_codes: tuple[str, ...]
    treatment_count: int = Field(ge=1, le=20)
    control_count: int = Field(ge=0, le=20)
    treatment_confirmed_count: int | None
    control_confirmed_count: int | None
    treatment_confirmation_bps: int | None
    control_confirmation_bps: int | None
    confirmation_lift_bps: int | None
    terminal_at: dt.datetime

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        metrics = (
            self.treatment_confirmed_count,
            self.control_confirmed_count,
            self.treatment_confirmation_bps,
            self.control_confirmation_bps,
            self.confirmation_lift_bps,
        )
        if (
            not self.trial_id
            or not self.strategy_version
            or _HEX64.fullmatch(self.cohort_artifact_id) is None
            or not _canonical_set(self.reason_codes)
            or not _aware(self.terminal_at)
        ):
            raise InvalidUsNewsCatalystTrialModelError
        match self.terminal_kind:
            case TrialEventKind.COMPLETED:
                if not self._completed_valid(metrics):
                    raise InvalidUsNewsCatalystTrialModelError
            case TrialEventKind.CENSORED | TrialEventKind.FAILED:
                if not self.reason_codes or self.observation_manifest_id is not None or any(
                    value is not None for value in metrics
                ):
                    raise InvalidUsNewsCatalystTrialModelError
            case TrialEventKind.STARTED:
                raise InvalidUsNewsCatalystTrialModelError
            case unreachable:
                assert_never(unreachable)
        return self

    def _completed_valid(self, metrics: tuple[int | None, ...]) -> bool:
        treatment, control, treatment_bps, control_bps, lift = metrics
        if None in metrics:
            return False
        assert treatment is not None
        assert control is not None
        assert treatment_bps is not None
        assert control_bps is not None
        assert lift is not None
        return (
            self.observation_manifest_id is not None
            and _HEX64.fullmatch(self.observation_manifest_id) is not None
            and not self.reason_codes
            and self.control_count == self.treatment_count
            and 0 <= treatment <= self.treatment_count
            and 0 <= control <= self.control_count
            and treatment_bps == treatment * 10_000 // self.treatment_count
            and control_bps == control * 10_000 // self.control_count
            and lift == treatment_bps - control_bps
        )


class UsNewsCatalystTrialOutcomeArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str
    payload: UsNewsCatalystTrialOutcomePayload

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        if self.artifact_id != _payload_id(self.payload):
            raise InvalidUsNewsCatalystTrialModelError
        return self


def create_us_news_catalyst_setup_observation_manifest(
    *,
    trial_id: str,
    cohort_artifact_id: str,
    evaluator_version: str,
    observations: tuple[UsNewsCatalystSetupFeatureObservation, ...],
) -> UsNewsCatalystSetupObservationManifest:
    ordered = tuple(sorted(observations, key=lambda item: item.symbol))
    return UsNewsCatalystSetupObservationManifest(
        manifest_id=_setup_manifest_id(trial_id, cohort_artifact_id, evaluator_version, ordered),
        trial_id=trial_id,
        cohort_artifact_id=cohort_artifact_id,
        evaluator_version=evaluator_version,
        observations=ordered,
    )


def trial_outcome_artifact(
    payload: UsNewsCatalystTrialOutcomePayload,
) -> UsNewsCatalystTrialOutcomeArtifact:
    return UsNewsCatalystTrialOutcomeArtifact(artifact_id=_payload_id(payload), payload=payload)


def _manifest_id(manifest: UsNewsCatalystSetupObservationManifest) -> str:
    return _setup_manifest_id(
        manifest.trial_id,
        manifest.cohort_artifact_id,
        manifest.evaluator_version,
        manifest.observations,
    )


def _setup_manifest_id(
    trial_id: str,
    cohort_artifact_id: str,
    evaluator_version: str,
    observations: tuple[UsNewsCatalystSetupFeatureObservation, ...],
) -> str:
    payload = {
        "cohort_artifact_id": cohort_artifact_id,
        "evaluator_version": evaluator_version,
        "observations": tuple(item.model_dump(mode="json") for item in observations),
        "trial_id": trial_id,
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _positive(value: Decimal) -> bool:
    return value.is_finite() and value > 0


__all__ = (
    "US_NEWS_CATALYST_EVALUATOR_VERSION",
    "US_NEWS_CATALYST_SETUP_HORIZON",
    "UsNewsCatalystSetupFeatureObservation",
    "UsNewsCatalystSetupObservationManifest",
    "UsNewsCatalystTrialOutcomeArtifact",
    "UsNewsCatalystTrialOutcomePayload",
    "create_us_news_catalyst_setup_observation_manifest",
    "trial_outcome_artifact",
)
