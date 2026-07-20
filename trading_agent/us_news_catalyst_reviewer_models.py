from __future__ import annotations

import datetime as dt
import hashlib
import re
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

US_NEWS_CATALYST_REVIEWER_VERSION = "us_news_catalyst_reviewer_v1"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class InvalidUsNewsCatalystReviewerModelError(ValueError):
    @override
    def __str__(self) -> str:
        return "US news-catalyst Reviewer model is invalid"


class UsNewsCatalystReviewerAction(StrEnum):
    COMPARISON_READY = "comparison_ready"
    CONTINUE_COLLECTION = "continue_collection"
    DATA_QUALITY_REVIEW = "data_quality_review"


class UsNewsCatalystReviewPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_version: str
    as_of_session: dt.date
    reviewer_version: str
    reviewed_at: dt.datetime
    included_trial_ids: tuple[str, ...]
    completed_session_count: int = Field(ge=0)
    censored_session_count: int = Field(ge=0)
    failed_session_count: int = Field(ge=0)
    missing_terminal_count: int = Field(ge=0)
    treatment_observation_count: int = Field(ge=0)
    control_observation_count: int = Field(ge=0)
    treatment_confirmed_count: int = Field(ge=0)
    control_confirmed_count: int = Field(ge=0)
    treatment_confirmation_bps: int | None
    control_confirmation_bps: int | None
    confirmation_lift_bps: int | None
    action: UsNewsCatalystReviewerAction
    reason_codes: tuple[str, ...]
    automatic_state_change_allowed: Literal[False] = False
    order_authority_change_allowed: Literal[False] = False
    allocation_change_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        total_sessions = (
            self.completed_session_count
            + self.censored_session_count
            + self.failed_session_count
            + self.missing_terminal_count
        )
        metrics = (
            self.treatment_confirmation_bps,
            self.control_confirmation_bps,
            self.confirmation_lift_bps,
        )
        if (
            not self.strategy_version
            or self.reviewer_version != US_NEWS_CATALYST_REVIEWER_VERSION
            or not _aware(self.reviewed_at)
            or self.included_trial_ids != tuple(sorted(set(self.included_trial_ids)))
            or total_sessions != len(self.included_trial_ids)
            or self.treatment_observation_count != self.control_observation_count
            or self.treatment_confirmed_count > self.treatment_observation_count
            or self.control_confirmed_count > self.control_observation_count
            or self.reason_codes != tuple(sorted(set(self.reason_codes)))
            or not self.reason_codes
            or not self._metrics_valid(metrics)
        ):
            raise InvalidUsNewsCatalystReviewerModelError
        return self

    def _metrics_valid(self, metrics: tuple[int | None, ...]) -> bool:
        treatment_bps, control_bps, lift = metrics
        if self.treatment_observation_count == 0:
            return all(value is None for value in metrics)
        if treatment_bps is None or control_bps is None or lift is None:
            return False
        return (
            treatment_bps
            == self.treatment_confirmed_count * 10_000 // self.treatment_observation_count
            and control_bps
            == self.control_confirmed_count * 10_000 // self.control_observation_count
            and lift == treatment_bps - control_bps
        )


class UsNewsCatalystReviewArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str
    payload: UsNewsCatalystReviewPayload

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if _HEX64.fullmatch(self.artifact_id) is None or self.artifact_id != expected:
            raise InvalidUsNewsCatalystReviewerModelError
        return self


def review_artifact(payload: UsNewsCatalystReviewPayload) -> UsNewsCatalystReviewArtifact:
    checked = UsNewsCatalystReviewPayload.model_validate(payload.model_dump())
    identity = hashlib.sha256(canonical_experiment_ledger_json(checked).encode()).hexdigest()
    return UsNewsCatalystReviewArtifact(artifact_id=identity, payload=checked)


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "US_NEWS_CATALYST_REVIEWER_VERSION",
    "InvalidUsNewsCatalystReviewerModelError",
    "UsNewsCatalystReviewArtifact",
    "UsNewsCatalystReviewPayload",
    "UsNewsCatalystReviewerAction",
    "review_artifact",
)
