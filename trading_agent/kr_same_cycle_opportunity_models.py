from __future__ import annotations

import datetime as dt
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_theme_keyword import KrKeywordRuleSet
from trading_agent.kr_theme_projection_manifest import LoadedKrThemeProjectionRun
from trading_agent.kr_theme_research_registration import kr_theme_strategy_version

SAFE_KR_OPPORTUNITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class InvalidKrSameCycleOpportunityRunError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR same-cycle Opportunity run is invalid"


class KrSameCycleOpportunityPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    producer_strategy_version: str
    runtime_code_version: str
    validity_seconds: int
    maximum_cycle_age_seconds: int
    rules: KrKeywordRuleSet

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if (
            SAFE_KR_OPPORTUNITY_ID.fullmatch(self.runtime_code_version) is None
            or self.producer_strategy_version != kr_theme_strategy_version(self.runtime_code_version)
            or not 1 <= self.validity_seconds <= 3_600
            or not 1 <= self.maximum_cycle_age_seconds <= 300
        ):
            raise InvalidKrSameCycleOpportunityRunError
        return self


@dataclass(frozen=True, slots=True)
class KrSameCycleOpportunityPreparation:
    collection_cycle_id: str
    collection_date: dt.date
    prepared_at: dt.datetime
    run_root: Path


@dataclass(frozen=True, slots=True)
class PreparedKrSameCycleOpportunityRun:
    run_manifest: Path
    loaded: LoadedKrThemeProjectionRun
    replayed: bool


def load_kr_same_cycle_opportunity_policy(
    path: Path,
) -> KrSameCycleOpportunityPolicy:
    try:
        candidate = path.expanduser().absolute()
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError
        return KrSameCycleOpportunityPolicy.model_validate_json(candidate.read_bytes())
    except (OSError, UnicodeError, ValidationError, ValueError):
        raise InvalidKrSameCycleOpportunityRunError from None


__all__ = (
    "SAFE_KR_OPPORTUNITY_ID",
    "InvalidKrSameCycleOpportunityRunError",
    "KrSameCycleOpportunityPolicy",
    "KrSameCycleOpportunityPreparation",
    "PreparedKrSameCycleOpportunityRun",
    "load_kr_same_cycle_opportunity_policy",
)
