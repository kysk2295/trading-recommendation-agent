from __future__ import annotations

import datetime as dt
import re
from typing import Self, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.data_capability_models import DataSourceId

_EVENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,63}$")
_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class CanonicalHistoryCoverageError(ValueError):
    @override
    def __str__(self) -> str:
        return "canonical history coverage is invalid"


class IncompleteCanonicalHistoryError(CanonicalHistoryCoverageError):
    @override
    def __str__(self) -> str:
        return "canonical history is incomplete"


class CanonicalHistoryCoverage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    source_id: DataSourceId
    event_type: str
    observed_from: dt.datetime
    observed_through: dt.datetime
    raw_first_verified: bool
    correction_required: bool
    correction_supported: bool
    correction_observed: bool
    tombstone_required: bool
    tombstone_supported: bool
    tombstone_observed: bool
    continuity_attested: bool
    complete_history: bool
    reason_codes: tuple[str, ...]

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        factors = (
            self.raw_first_verified,
            not self.correction_required or self.correction_supported,
            not self.tombstone_required or self.tombstone_supported,
            self.continuity_attested,
        )
        expected_reasons = tuple(
            sorted(
                reason
                for reason, satisfied in (
                    ("raw_first_unverified", factors[0]),
                    ("correction_unsupported", factors[1]),
                    ("tombstone_unsupported", factors[2]),
                    ("continuity_unattested", factors[3]),
                )
                if not satisfied
            )
        )
        aware = all(
            value.tzinfo is not None and value.utcoffset() is not None
            for value in (self.observed_from, self.observed_through)
        )
        if (
            _EVENT_TYPE.fullmatch(self.event_type) is None
            or not aware
            or self.observed_from > self.observed_through
            or self.complete_history is not all(factors)
            or self.reason_codes != expected_reasons
            or any(_REASON.fullmatch(reason) is None for reason in self.reason_codes)
        ):
            raise CanonicalHistoryCoverageError
        return self


def require_complete_canonical_history(
    coverage: CanonicalHistoryCoverage,
) -> CanonicalHistoryCoverage:
    try:
        checked = CanonicalHistoryCoverage.model_validate(coverage.model_dump(mode="python"))
    except (AttributeError, TypeError, ValueError):
        raise CanonicalHistoryCoverageError from None
    if not checked.complete_history:
        raise IncompleteCanonicalHistoryError
    return checked


__all__ = (
    "CanonicalHistoryCoverage",
    "CanonicalHistoryCoverageError",
    "IncompleteCanonicalHistoryError",
    "require_complete_canonical_history",
)
