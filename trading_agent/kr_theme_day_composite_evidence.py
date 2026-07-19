from __future__ import annotations

import datetime as dt
from typing import Final, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.experiment_ledger_store import ExperimentLedgerReader
from trading_agent.kr_theme_day_composite import (
    InvalidKrThemeDayCompositeError,
    KrThemeDayCompositeAuthority,
    KrThemeDayCompositeAuthorityRequest,
    require_exact_kr_theme_day_composite,
)

_HYPOTHESIS_PREFIX: Final = "composite_hypothesis:"
_REGISTRATION_PREFIX: Final = "composite_registration:"
_OPPORTUNITY_PREFIX: Final = "opportunity_strategy:"


class InvalidKrThemeDayCompositeEvidenceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR theme day composite evidence is invalid"


class KrThemeDayCompositeEvidenceRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    day_strategy_version: str
    evidence_budget: tuple[str, ...]
    as_of: dt.datetime

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            not self.day_strategy_version
            or self.day_strategy_version != self.day_strategy_version.strip()
            or not self.evidence_budget
            or not _aware(self.as_of)
        ):
            raise InvalidKrThemeDayCompositeEvidenceError
        return self


def kr_theme_day_composite_evidence(authority: KrThemeDayCompositeAuthority) -> tuple[str, ...]:
    return tuple(
        sorted(
            (
                f"{_HYPOTHESIS_PREFIX}{authority.hypothesis_id}",
                f"{_REGISTRATION_PREFIX}{authority.registration_key}",
                f"{_OPPORTUNITY_PREFIX}{authority.opportunity_strategy_version}",
            )
        )
    )


def require_exact_kr_theme_day_composite_evidence(
    ledger: ExperimentLedgerReader,
    request: KrThemeDayCompositeEvidenceRequest,
) -> KrThemeDayCompositeAuthority:
    try:
        request = KrThemeDayCompositeEvidenceRequest.model_validate(request.model_dump(mode="python"))
        opportunity_version = _one(request.evidence_budget, _OPPORTUNITY_PREFIX)
        authority = require_exact_kr_theme_day_composite(
            ledger,
            KrThemeDayCompositeAuthorityRequest(
                day_strategy_version=request.day_strategy_version,
                opportunity_strategy_version=opportunity_version,
                as_of=request.as_of,
            ),
        )
        if (
            _one(request.evidence_budget, _HYPOTHESIS_PREFIX) != authority.hypothesis_id
            or _one(request.evidence_budget, _REGISTRATION_PREFIX) != authority.registration_key
        ):
            raise InvalidKrThemeDayCompositeEvidenceError
        return authority
    except (
        AttributeError,
        InvalidKrThemeDayCompositeError,
        ValidationError,
        ValueError,
    ):
        raise InvalidKrThemeDayCompositeEvidenceError from None


def _one(evidence_budget: tuple[str, ...], prefix: str) -> str:
    matches = tuple(value.removeprefix(prefix) for value in evidence_budget if value.startswith(prefix))
    if len(matches) != 1 or not matches[0]:
        raise InvalidKrThemeDayCompositeEvidenceError
    return matches[0]


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
