from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Literal, Self, assert_never

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.lane_identity_models import LaneId
from trading_agent.research_identity_models import (
    AgentFamily,
    AgentOperatingMode,
    LegacyExecutionLaneBinding,
    MarketId,
    StrategyLaneRef,
)

_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class InvalidStrategyAuthorityIdentityError(ValueError):
    def __str__(self) -> str:
        return "invalid strategy authority identity"


@dataclass(frozen=True, slots=True)
class InvalidStrategyAuthorityExecutionBindingError(ValueError):
    def __str__(self) -> str:
        return "invalid strategy authority execution binding"


@dataclass(frozen=True, slots=True)
class StrategyAuthorityPaperEligibilityError(ValueError):
    def __str__(self) -> str:
        return "strategy authority is not Alpaca Paper eligible"


class StrategyAuthorityBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_version: str
    strategy_lane: StrategyLaneRef
    operating_mode: AgentOperatingMode
    legacy_lane_id: LaneId
    bound_at: dt.datetime

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if _VERSION.fullmatch(self.strategy_version) is None or not _aware(self.bound_at):
            raise InvalidStrategyAuthorityIdentityError
        try:
            _ = LegacyExecutionLaneBinding(
                strategy_lane=self.strategy_lane,
                legacy_lane_id=self.legacy_lane_id,
            )
        except ValueError:
            raise InvalidStrategyAuthorityExecutionBindingError from None
        match self.operating_mode:
            case AgentOperatingMode.CONTRACT_ONLY | AgentOperatingMode.SHADOW:
                return self
            case AgentOperatingMode.ALPACA_PAPER:
                if not _paper_authorized(self.strategy_lane):
                    raise StrategyAuthorityPaperEligibilityError
                return self
            case unreachable:
                assert_never(unreachable)


def _paper_authorized(strategy_lane: StrategyLaneRef) -> bool:
    return strategy_lane.market_id is MarketId.US_EQUITIES and strategy_lane.agent_family in {
        AgentFamily.DAY_TRADING,
        AgentFamily.SWING_TRADING,
    }


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
