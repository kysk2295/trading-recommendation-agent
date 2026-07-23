from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.intraday_walk_forward_models import IntradayWalkForwardResult
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
    read_private_text,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class InvalidIntradayResearchArtifactError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday historical experiment artifact is invalid"


class IntradayExperimentPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1, 2] = 2
    trial_id: str
    strategy_version: str
    evaluator_version: str
    data_version: str
    manifest_sha256: str
    registered_at: dt.datetime
    started_at: dt.datetime
    completed_at: dt.datetime
    result: IntradayWalkForwardResult

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if (
            not self.trial_id
            or not self.strategy_version
            or not self.evaluator_version
            or _HEX64.fullmatch(self.data_version) is None
            or _HEX64.fullmatch(self.manifest_sha256) is None
            or not _aware(self.registered_at)
            or not _aware(self.started_at)
            or not _aware(self.completed_at)
            or not self.registered_at <= self.started_at <= self.completed_at
            or self.result.schema_version != self.schema_version
        ):
            raise InvalidIntradayResearchArtifactError
        return self


class IntradayExperimentArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1, 2] = 2
    artifact_id: str
    payload: IntradayExperimentPayload

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if self.artifact_id != expected or self.schema_version != self.payload.schema_version:
            raise InvalidIntradayResearchArtifactError
        return self


def intraday_experiment_artifact(payload: IntradayExperimentPayload) -> IntradayExperimentArtifact:
    checked = IntradayExperimentPayload.model_validate(payload.model_dump())
    identity = hashlib.sha256(canonical_experiment_ledger_json(checked).encode()).hexdigest()
    return IntradayExperimentArtifact(
        schema_version=checked.schema_version,
        artifact_id=identity,
        payload=checked,
    )


def publish_intraday_experiment_artifact(
    root: Path,
    artifact: IntradayExperimentArtifact,
) -> tuple[Path, bool]:
    try:
        checked = IntradayExperimentArtifact.model_validate(artifact.model_dump())
        path = root / f"intraday_walk_forward_{checked.artifact_id}.json"
        created = publish_private_immutable_text(path, _payload(checked))
        return path, created
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidIntradayResearchArtifactError from None


def load_intraday_experiment_artifact(path: Path) -> IntradayExperimentArtifact:
    try:
        payload = read_private_text(path)
        artifact = IntradayExperimentArtifact.model_validate_json(payload)
        if path.name != f"intraday_walk_forward_{artifact.artifact_id}.json" or payload != _payload(artifact):
            raise InvalidIntradayResearchArtifactError
        return artifact
    except InvalidIntradayResearchArtifactError:
        raise
    except (InvalidPrivateImmutableFileError, TypeError, ValidationError, ValueError):
        raise InvalidIntradayResearchArtifactError from None


def _payload(artifact: IntradayExperimentArtifact) -> str:
    return canonical_experiment_ledger_json(artifact) + "\n"


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "IntradayExperimentArtifact",
    "IntradayExperimentPayload",
    "InvalidIntradayResearchArtifactError",
    "intraday_experiment_artifact",
    "load_intraday_experiment_artifact",
    "publish_intraday_experiment_artifact",
)
