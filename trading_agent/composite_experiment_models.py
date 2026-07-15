from __future__ import annotations

import datetime as dt
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.research_identity_models import StrategyLaneRef

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class StrategyVersionRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_lane: StrategyLaneRef
    strategy_version: str

    @model_validator(mode="after")
    def validate_version(self) -> Self:
        if _IDENTIFIER.fullmatch(self.strategy_version) is None:
            raise ValueError("invalid strategy version identity")
        return self

    @property
    def canonical_id(self) -> str:
        return f"{self.strategy_lane.canonical_id}@{self.strategy_version}"


class CompositeExperimentSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    experiment_id: str
    primary_lane: StrategyLaneRef
    component_versions: tuple[StrategyVersionRef, ...]
    combination_rule: str
    registered_at: dt.datetime
    effective_at: dt.datetime

    @model_validator(mode="after")
    def validate_spec(self) -> Self:
        component_ids = tuple(component.canonical_id for component in self.component_versions)
        lane_ids = tuple(component.strategy_lane.canonical_id for component in self.component_versions)
        primary_market = self.primary_lane.market_id
        if (
            _IDENTIFIER.fullmatch(self.experiment_id) is None
            or not _canonical_text(self.combination_rule)
            or not _aware(self.registered_at)
            or not _aware(self.effective_at)
            or self.effective_at <= self.registered_at
            or len(component_ids) < 2
            or component_ids != tuple(sorted(set(component_ids)))
            or len(set(lane_ids)) < 2
            or self.primary_lane.canonical_id not in lane_ids
            or any(component.strategy_lane.market_id is not primary_market for component in self.component_versions)
        ):
            raise ValueError("invalid composite experiment specification")
        return self


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str) -> bool:
    return bool(value) and value == value.strip() and len(value) <= 2000
