from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.lane_identity_models import LaneId

_STRATEGY_ID = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class InvalidResearchIdentityError(ValueError):
    reason: str

    def __str__(self) -> str:
        return self.reason


class MarketId(StrEnum):
    US_EQUITIES = "us_equities"
    KR_EQUITIES = "kr_equities"


class AgentFamily(StrEnum):
    OPPORTUNITY_MANAGER = "opportunity_manager"
    DAY_TRADING = "day_trading"
    SWING_TRADING = "swing_trading"
    SYSTEMATIC_QUANT = "systematic_quant"
    DERIVATIVES_RESEARCH = "derivatives_research"
    MARKET_CONTEXT = "market_context"
    ALLOCATION_MANAGER = "allocation_manager"


RUNTIME_RESEARCH_AGENT_FAMILIES: Final[tuple[AgentFamily, ...]] = (
    AgentFamily.OPPORTUNITY_MANAGER,
    AgentFamily.DAY_TRADING,
    AgentFamily.SWING_TRADING,
    AgentFamily.SYSTEMATIC_QUANT,
    AgentFamily.DERIVATIVES_RESEARCH,
    AgentFamily.MARKET_CONTEXT,
)


class AgentOutputKind(StrEnum):
    OPPORTUNITY = "opportunity"
    TRADE_SIGNAL = "trade_signal"
    DERIVATIVES_RESEARCH = "derivatives_research"
    MARKET_CONTEXT = "market_context"
    ALLOCATION = "allocation"


class AgentOperatingMode(StrEnum):
    CONTRACT_ONLY = "contract_only"
    SHADOW = "shadow"
    ALPACA_PAPER = "alpaca_paper"


class StrategyLaneRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    market_id: MarketId
    agent_family: AgentFamily
    strategy_id: str

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        if _STRATEGY_ID.fullmatch(self.strategy_id) is None:
            raise InvalidResearchIdentityError(reason="invalid strategy lane identity")
        return self

    @property
    def canonical_id(self) -> str:
        return f"{self.market_id.value}/{self.agent_family.value}/{self.strategy_id}"


class AgentManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    market_id: MarketId
    agent_family: AgentFamily
    manifest_version: str
    registered_at: dt.datetime
    output_kind: AgentOutputKind
    operating_mode: AgentOperatingMode
    strategy_lanes: tuple[StrategyLaneRef, ...]

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        lane_ids = tuple(lane.canonical_id for lane in self.strategy_lanes)
        expected_output = {
            AgentFamily.OPPORTUNITY_MANAGER: AgentOutputKind.OPPORTUNITY,
            AgentFamily.DAY_TRADING: AgentOutputKind.TRADE_SIGNAL,
            AgentFamily.SWING_TRADING: AgentOutputKind.TRADE_SIGNAL,
            AgentFamily.SYSTEMATIC_QUANT: AgentOutputKind.TRADE_SIGNAL,
            AgentFamily.DERIVATIVES_RESEARCH: AgentOutputKind.DERIVATIVES_RESEARCH,
            AgentFamily.MARKET_CONTEXT: AgentOutputKind.MARKET_CONTEXT,
            AgentFamily.ALLOCATION_MANAGER: AgentOutputKind.ALLOCATION,
        }[self.agent_family]
        paper_authorized = (
            self.market_id is MarketId.US_EQUITIES
            and self.agent_family in {AgentFamily.DAY_TRADING, AgentFamily.SWING_TRADING}
        )
        if (
            not _aware(self.registered_at)
            or _VERSION.fullmatch(self.manifest_version) is None
            or not self.strategy_lanes
            or lane_ids != tuple(sorted(set(lane_ids)))
            or any(
                lane.market_id is not self.market_id or lane.agent_family is not self.agent_family
                for lane in self.strategy_lanes
            )
            or self.output_kind is not expected_output
            or (self.operating_mode is AgentOperatingMode.ALPACA_PAPER and not paper_authorized)
        ):
            raise InvalidResearchIdentityError(reason="invalid agent manifest")
        return self


class LegacyExecutionLaneBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_lane: StrategyLaneRef
    legacy_lane_id: LaneId

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        expected = {
            (MarketId.US_EQUITIES, AgentFamily.DAY_TRADING): LaneId.INTRADAY_MOMENTUM,
            (MarketId.US_EQUITIES, AgentFamily.SWING_TRADING): LaneId.SWING_MOMENTUM,
            (MarketId.US_EQUITIES, AgentFamily.MARKET_CONTEXT): LaneId.MARKET_REGIME,
        }.get((self.strategy_lane.market_id, self.strategy_lane.agent_family))
        if expected is None or self.legacy_lane_id is not expected:
            raise InvalidResearchIdentityError(
                reason="strategy lane has no approved legacy execution binding"
            )
        return self


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
