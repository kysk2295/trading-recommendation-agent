from __future__ import annotations

import datetime as dt
import math
import re
from enum import StrEnum
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class InvalidIntradayParameterPlateauError(ValueError):
    @override
    def __str__(self) -> str:
        return "intraday parameter plateau evidence is invalid"


class IntradayParameterPlateauStatus(StrEnum):
    COLLECTING = "collecting"
    PLATEAU_READY = "plateau_ready"
    PLATEAU_NOT_FOUND = "plateau_not_found"


class IntradayParameterPlateauVariantTrace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    variant_id: str
    parameter_set: tuple[str, ...]
    is_center: bool
    session_dates: tuple[dt.date, ...]
    net_trade_returns_by_session: tuple[tuple[float, ...], ...]
    trade_count: int = Field(ge=0, le=100_000)
    average_return: float | None

    @model_validator(mode="after")
    def validate_trace(self) -> Self:
        flattened = tuple(
            value
            for session in self.net_trade_returns_by_session
            for value in session
        )
        expected_average = (
            None if not flattened else sum(flattened) / len(flattened)
        )
        if (
            _IDENTIFIER.fullmatch(self.variant_id) is None
            or self.is_center is not (self.variant_id == "center")
            or not _parameter_set_is_canonical(self.parameter_set)
            or not 1 <= len(self.session_dates) <= 60
            or self.session_dates != tuple(sorted(set(self.session_dates)))
            or len(self.session_dates)
            != len(self.net_trade_returns_by_session)
            or len(flattened) != self.trade_count
            or any(
                not math.isfinite(value) or value <= -1.0
                for value in flattened
            )
            or not _optional_close(self.average_return, expected_average)
        ):
            raise InvalidIntradayParameterPlateauError
        return self


def _parameter_set_is_canonical(values: tuple[str, ...]) -> bool:
    return (
        bool(values)
        and len(values) == len(set(values))
        and all(
            "=" in value
            and value == value.strip()
            and not any(char in value for char in "\r\n\t")
            for value in values
        )
    )


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
    "IntradayParameterPlateauStatus",
    "IntradayParameterPlateauVariantTrace",
    "InvalidIntradayParameterPlateauError",
)
