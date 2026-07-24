from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.daily_research_contract import strategy_contract
from trading_agent.intraday_parameter_plateau_statistics import (
    derive_parameter_plateau_statistics,
)
from trading_agent.intraday_parameter_plateau_trace_models import (
    IntradayParameterPlateauStatus,
    IntradayParameterPlateauVariantTrace,
    InvalidIntradayParameterPlateauError,
)
from trading_agent.intraday_parameter_plateau_variants import (
    parameter_variants,
)
from trading_agent.strategy_factory import StrategyMode

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class IntradayParameterPlateauAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy: StrategyMode
    trial_id: str
    strategy_version: str
    experiment_artifact_id: str
    registered_parameter_set: tuple[str, ...]
    variants: tuple[IntradayParameterPlateauVariantTrace, ...]
    status: IntradayParameterPlateauStatus
    blockers: tuple[str, ...]
    observed_sessions: int = Field(ge=1, le=60)
    center_trade_count: int = Field(ge=0, le=100_000)
    center_average_return: float | None
    eligible_neighbor_count: int = Field(ge=0, le=6)
    positive_neighbor_count: int = Field(ge=0, le=6)
    positive_neighbor_rate: float | None = Field(ge=0.0, le=1.0)
    neighbor_average_return_min: float | None
    neighbor_average_return_median: float | None
    neighbor_average_return_max: float | None

    @model_validator(mode="after")
    def validate_analysis(self) -> Self:
        _require_analysis_identity(
            IntradayParameterPlateauAnalysisRequest(
                strategy=self.strategy,
                trial_id=self.trial_id,
                strategy_version=self.strategy_version,
                experiment_artifact_id=self.experiment_artifact_id,
                registered_parameter_set=self.registered_parameter_set,
                variants=self.variants,
            )
        )
        derived = derive_parameter_plateau_statistics(self.variants)
        actual = (
            self.center_average_return,
            self.positive_neighbor_rate,
            self.neighbor_average_return_min,
            self.neighbor_average_return_median,
            self.neighbor_average_return_max,
        )
        expected = (
            derived.center_average_return,
            derived.positive_neighbor_rate,
            derived.neighbor_average_return_min,
            derived.neighbor_average_return_median,
            derived.neighbor_average_return_max,
        )
        if (
            self.status is not derived.status
            or self.blockers != derived.blockers
            or self.observed_sessions != derived.observed_sessions
            or self.center_trade_count != derived.center_trade_count
            or self.eligible_neighbor_count
            != derived.eligible_neighbor_count
            or self.positive_neighbor_count
            != derived.positive_neighbor_count
            or not all(
                _optional_close(value, target)
                for value, target in zip(actual, expected, strict=True)
            )
        ):
            raise InvalidIntradayParameterPlateauError
        return self


@dataclass(frozen=True, slots=True)
class IntradayParameterPlateauAnalysisRequest:
    strategy: StrategyMode
    trial_id: str
    strategy_version: str
    experiment_artifact_id: str
    registered_parameter_set: tuple[str, ...]
    variants: tuple[IntradayParameterPlateauVariantTrace, ...]


def calculate_intraday_parameter_plateau_analysis(
    request: IntradayParameterPlateauAnalysisRequest,
) -> IntradayParameterPlateauAnalysis:
    checked = tuple(
        IntradayParameterPlateauVariantTrace.model_validate(
            variant.model_dump()
        )
        for variant in request.variants
    )
    checked_request = IntradayParameterPlateauAnalysisRequest(
        strategy=request.strategy,
        trial_id=request.trial_id,
        strategy_version=request.strategy_version,
        experiment_artifact_id=request.experiment_artifact_id,
        registered_parameter_set=request.registered_parameter_set,
        variants=checked,
    )
    _require_analysis_identity(checked_request)
    derived = derive_parameter_plateau_statistics(checked)
    return IntradayParameterPlateauAnalysis(
        strategy=request.strategy,
        trial_id=request.trial_id,
        strategy_version=request.strategy_version,
        experiment_artifact_id=request.experiment_artifact_id,
        registered_parameter_set=request.registered_parameter_set,
        variants=checked,
        status=derived.status,
        blockers=derived.blockers,
        observed_sessions=derived.observed_sessions,
        center_trade_count=derived.center_trade_count,
        center_average_return=derived.center_average_return,
        eligible_neighbor_count=derived.eligible_neighbor_count,
        positive_neighbor_count=derived.positive_neighbor_count,
        positive_neighbor_rate=derived.positive_neighbor_rate,
        neighbor_average_return_min=(
            derived.neighbor_average_return_min
        ),
        neighbor_average_return_median=(
            derived.neighbor_average_return_median
        ),
        neighbor_average_return_max=(
            derived.neighbor_average_return_max
        ),
    )


def _require_analysis_identity(
    request: IntradayParameterPlateauAnalysisRequest,
) -> None:
    expected_identity = tuple(
        (
            variant.variant_id,
            variant.parameter_set,
            variant.is_center,
        )
        for variant in parameter_variants(request.strategy)
    )
    actual_identity = tuple(
        (
            variant.variant_id,
            variant.parameter_set,
            variant.is_center,
        )
        for variant in request.variants
    )
    dates = tuple(variant.session_dates for variant in request.variants)
    if (
        _IDENTIFIER.fullmatch(request.trial_id) is None
        or _IDENTIFIER.fullmatch(request.strategy_version) is None
        or _HEX64.fullmatch(request.experiment_artifact_id) is None
        or request.registered_parameter_set
        != strategy_contract(request.strategy).parameter_set
        or actual_identity != expected_identity
        or not dates
        or any(value != dates[0] for value in dates[1:])
    ):
        raise InvalidIntradayParameterPlateauError


def _optional_close(
    actual: float | None,
    expected: float | None,
) -> bool:
    if actual is None or expected is None:
        return actual is expected
    return math.isfinite(actual) and math.isclose(
        actual,
        expected,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )


__all__ = (
    "IntradayParameterPlateauAnalysis",
    "IntradayParameterPlateauAnalysisRequest",
    "calculate_intraday_parameter_plateau_analysis",
)
