from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import (
    canonical_experiment_ledger_json,
)
from trading_agent.intraday_parameter_plateau_models import (
    IntradayParameterPlateauAnalysis,
    IntradayParameterPlateauStatus,
    InvalidIntradayParameterPlateauError,
)

INTRADAY_PARAMETER_PLATEAU_VERSION: Final = (
    "intraday_adjacent_parameter_plateau_v1"
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class IntradayParameterPlateauPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    evaluator_version: Literal[
        "intraday_adjacent_parameter_plateau_v1"
    ]
    reviewed_at: dt.datetime
    data_version: str
    manifest_sha256: str
    side_cost_bps: int = Field(ge=20, le=100)
    status: IntradayParameterPlateauStatus
    analyses: tuple[IntradayParameterPlateauAnalysis, ...]
    automatic_state_change_allowed: Literal[False] = False
    order_authority_change_allowed: Literal[False] = False
    allocation_change_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        ordered = tuple(
            sorted(
                self.analyses,
                key=lambda analysis: analysis.strategy_version,
            )
        )
        identities = (
            tuple(analysis.strategy for analysis in self.analyses),
            tuple(analysis.trial_id for analysis in self.analyses),
            tuple(
                analysis.strategy_version
                for analysis in self.analyses
            ),
            tuple(
                analysis.experiment_artifact_id
                for analysis in self.analyses
            ),
        )
        if (
            self.reviewed_at.tzinfo is None
            or self.reviewed_at.utcoffset() is None
            or _HEX64.fullmatch(self.data_version) is None
            or _HEX64.fullmatch(self.manifest_sha256) is None
            or not 1 <= len(self.analyses) <= 3
            or self.analyses != ordered
            or any(
                len(set(values)) != len(values)
                for values in identities
            )
            or self.status is not aggregate_parameter_plateau_status(
                self.analyses
            )
        ):
            raise InvalidIntradayParameterPlateauError
        return self


class IntradayParameterPlateauArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    artifact_id: str
    payload: IntradayParameterPlateauPayload

    @model_validator(mode="after")
    def validate_artifact(self) -> Self:
        expected = hashlib.sha256(
            canonical_experiment_ledger_json(self.payload).encode()
        ).hexdigest()
        if self.artifact_id != expected:
            raise InvalidIntradayParameterPlateauError
        return self


def aggregate_parameter_plateau_status(
    analyses: tuple[IntradayParameterPlateauAnalysis, ...],
) -> IntradayParameterPlateauStatus:
    statuses = tuple(analysis.status for analysis in analyses)
    if IntradayParameterPlateauStatus.PLATEAU_NOT_FOUND in statuses:
        return IntradayParameterPlateauStatus.PLATEAU_NOT_FOUND
    if IntradayParameterPlateauStatus.COLLECTING in statuses:
        return IntradayParameterPlateauStatus.COLLECTING
    if all(
        status is IntradayParameterPlateauStatus.PLATEAU_READY
        for status in statuses
    ):
        return IntradayParameterPlateauStatus.PLATEAU_READY
    return IntradayParameterPlateauStatus.PLATEAU_NOT_FOUND


__all__ = (
    "INTRADAY_PARAMETER_PLATEAU_VERSION",
    "IntradayParameterPlateauArtifact",
    "IntradayParameterPlateauPayload",
    "aggregate_parameter_plateau_status",
)
