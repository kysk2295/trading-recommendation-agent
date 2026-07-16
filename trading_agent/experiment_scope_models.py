from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.lane_identity_models import LaneId

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ExperimentScopeKind(StrEnum):
    SINGLE_LANE = "single_lane"
    CROSS_LANE_HYPOTHESIS = "cross_lane_hypothesis"


class ExperimentScope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    scope_kind: ExperimentScopeKind
    hypothesis_id: str
    primary_lane: LaneId
    lanes: tuple[LaneId, ...]
    source_hypothesis_ids: tuple[str, ...] = ()
    combination_rule: str | None = None
    registered_at: dt.datetime

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        lanes = tuple(sorted(set(self.lanes), key=str))
        sources = tuple(sorted(set(self.source_hypothesis_ids)))
        text_valid = _IDENTIFIER.fullmatch(self.hypothesis_id) is not None and all(
            _IDENTIFIER.fullmatch(source) for source in sources
        )
        if (
            not _aware(self.registered_at)
            or not text_valid
            or self.lanes != lanes
            or self.source_hypothesis_ids != sources
            or self.primary_lane not in lanes
        ):
            raise ValueError("invalid experiment scope identity")
        if self.scope_kind is ExperimentScopeKind.SINGLE_LANE:
            if lanes != (self.primary_lane,) or sources or self.combination_rule is not None:
                raise ValueError("single-lane scope cannot mix results")
            return self
        if (
            len(lanes) < 2
            or len(sources) < 2
            or self.hypothesis_id in sources
            or self.combination_rule is None
            or not self.combination_rule.strip()
            or self.combination_rule != self.combination_rule.strip()
        ):
            raise ValueError("cross-lane scope requires a new pre-registered hypothesis")
        return self


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = ("ExperimentScope", "ExperimentScopeKind")
