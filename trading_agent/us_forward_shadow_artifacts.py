from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self, cast, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from trading_agent.day_forward_trial_identity import DayForwardExitReason
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)
from trading_agent.signal_contract_models import TradeSignalEnvelope


class InvalidUsForwardShadowArtifactError(ValueError):
    @override
    def __str__(self) -> str:
        return "us_forward_shadow_artifact_invalid"


class UsForwardShadowArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class UsForwardShadowSignalArtifact(UsForwardShadowArtifact):
    schema_version: Literal[1] = 1
    artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    trial_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_bar_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    signal: TradeSignalEnvelope

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if self.artifact_id != _artifact_identity(self, "artifact_id"):
            raise InvalidUsForwardShadowArtifactError
        return self


class UsForwardShadowOutcomeArtifact(UsForwardShadowArtifact):
    schema_version: Literal[1] = 1
    outcome_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    trial_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    signal_artifact_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    exit_completed_bar_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    entry_price: Decimal
    exit_price: Decimal
    gross_return: Decimal
    round_trip_cost_bps: Decimal
    cost_adjusted_return: Decimal
    exit_reason: DayForwardExitReason
    recorded_at: AwareDatetime
    modeled: Literal[True] = True
    profitability_claim: Literal[False] = False

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        expected_gross = (self.exit_price - self.entry_price) / self.entry_price
        expected_adjusted = expected_gross - self.round_trip_cost_bps / Decimal(10_000)
        if (
            self.outcome_id != _artifact_identity(self, "outcome_id")
            or not self.entry_price.is_finite()
            or self.entry_price <= 0
            or not self.exit_price.is_finite()
            or self.exit_price <= 0
            or not self.round_trip_cost_bps.is_finite()
            or self.round_trip_cost_bps < 0
            or self.gross_return != expected_gross
            or self.cost_adjusted_return != expected_adjusted
        ):
            raise InvalidUsForwardShadowArtifactError
        return self


def build_us_forward_shadow_signal_artifact(
    *,
    trial_id: str,
    capsule_id: str,
    completed_bar_id: str,
    signal: TradeSignalEnvelope,
) -> UsForwardShadowSignalArtifact:
    payload = {
        "schema_version": 1,
        "artifact_id": "0" * 64,
        "trial_id": trial_id,
        "capsule_id": capsule_id,
        "completed_bar_id": completed_bar_id,
        "signal": signal,
    }
    provisional = UsForwardShadowSignalArtifact.model_construct(**payload)
    payload["artifact_id"] = _artifact_identity(provisional, "artifact_id")
    return UsForwardShadowSignalArtifact.model_validate(payload)


def build_us_forward_shadow_outcome_artifact(
    *,
    trial_id: str,
    signal_artifact_id: str,
    exit_completed_bar_id: str,
    entry_price: Decimal,
    exit_price: Decimal,
    round_trip_cost_bps: Decimal,
    exit_reason: DayForwardExitReason,
    recorded_at: AwareDatetime,
) -> UsForwardShadowOutcomeArtifact:
    gross_return = (exit_price - entry_price) / entry_price
    payload = {
        "schema_version": 1,
        "outcome_id": "0" * 64,
        "trial_id": trial_id,
        "signal_artifact_id": signal_artifact_id,
        "exit_completed_bar_id": exit_completed_bar_id,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_return": gross_return,
        "round_trip_cost_bps": round_trip_cost_bps,
        "cost_adjusted_return": gross_return - round_trip_cost_bps / Decimal(10_000),
        "exit_reason": exit_reason,
        "recorded_at": recorded_at,
        "modeled": True,
        "profitability_claim": False,
    }
    provisional = UsForwardShadowOutcomeArtifact.model_construct(**payload)
    payload["outcome_id"] = _artifact_identity(provisional, "outcome_id")
    return UsForwardShadowOutcomeArtifact.model_validate(payload)


class UsForwardShadowArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().absolute()

    def publish_signal(self, artifact: UsForwardShadowSignalArtifact) -> bool:
        try:
            checked = UsForwardShadowSignalArtifact.model_validate(artifact.model_dump(mode="python"))
            return self._publish(self._signal_path(checked.trial_id), checked)
        except (TypeError, ValidationError, ValueError):
            raise InvalidUsForwardShadowArtifactError from None

    def signal(self, trial_id: str) -> UsForwardShadowSignalArtifact:
        return self._load(self._signal_path(_checked_id(trial_id)), UsForwardShadowSignalArtifact)

    def publish_outcome(self, artifact: UsForwardShadowOutcomeArtifact) -> bool:
        try:
            checked = UsForwardShadowOutcomeArtifact.model_validate(artifact.model_dump(mode="python"))
            return self._publish(self._outcome_path(checked.outcome_id), checked)
        except (TypeError, ValidationError, ValueError):
            raise InvalidUsForwardShadowArtifactError from None

    def outcome(self, outcome_id: str) -> UsForwardShadowOutcomeArtifact:
        return self._load(self._outcome_path(_checked_id(outcome_id)), UsForwardShadowOutcomeArtifact)

    def _publish(self, path: Path, artifact: UsForwardShadowArtifact) -> bool:
        try:
            return publish_private_immutable_text(path, _canonical_payload(artifact))
        except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
            raise InvalidUsForwardShadowArtifactError from None

    def _load[ArtifactT: UsForwardShadowArtifact](
        self,
        path: Path,
        artifact_type: type[ArtifactT],
    ) -> ArtifactT:
        try:
            payload = read_private_text(path)
            artifact = artifact_type.model_validate_json(payload)
            if payload != _canonical_payload(artifact):
                raise InvalidUsForwardShadowArtifactError
            return artifact
        except InvalidUsForwardShadowArtifactError:
            raise
        except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
            raise InvalidUsForwardShadowArtifactError from None

    def _signal_path(self, trial_id: str) -> Path:
        return self.root / "signals" / f"{trial_id}.json"

    def _outcome_path(self, outcome_id: str) -> Path:
        return self.root / "outcomes" / f"{outcome_id}.json"


def artifact_sha256(artifact: UsForwardShadowArtifact) -> str:
    return hashlib.sha256(_canonical_payload(artifact).encode()).hexdigest()


def _artifact_identity(artifact: BaseModel, identity_field: str) -> str:
    payload = cast(dict[str, _CanonicalJson], artifact.model_dump(mode="json", exclude={identity_field}))
    encoded = canonical_experiment_ledger_json(_CanonicalPayload(payload=payload))
    return hashlib.sha256(encoded.encode()).hexdigest()


type _CanonicalJson = None | bool | int | float | str | list[_CanonicalJson] | dict[str, _CanonicalJson]


class _CanonicalPayload(BaseModel):
    payload: dict[str, _CanonicalJson]


def _canonical_payload(artifact: BaseModel) -> str:
    return canonical_experiment_ledger_json(artifact) + "\n"


def _checked_id(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise InvalidUsForwardShadowArtifactError
    return value


__all__ = (
    "InvalidUsForwardShadowArtifactError",
    "UsForwardShadowArtifactStore",
    "UsForwardShadowOutcomeArtifact",
    "UsForwardShadowSignalArtifact",
    "artifact_sha256",
    "build_us_forward_shadow_outcome_artifact",
    "build_us_forward_shadow_signal_artifact",
)
