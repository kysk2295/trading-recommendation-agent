from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.research_identity_models import AgentFamily, MarketId, StrategyLaneRef
from trading_agent.us_day_thesis_models import UsDayPlaybook


class InvalidReviewedUsDayStrategyManifestError(ValueError):
    pass


class ReviewedUsDayStrategyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    strategy_lane: StrategyLaneRef
    playbook: UsDayPlaybook
    reviewed: Literal[True]

    @model_validator(mode="after")
    def validate_lane(self) -> Self:
        if (
            self.strategy_lane.market_id is not MarketId.US_EQUITIES
            or self.strategy_lane.agent_family is not AgentFamily.DAY_TRADING
            or self.strategy_lane.strategy_id != self.capsule_id
            or self.playbook.playbook_id != self.capsule_id
        ):
            raise InvalidReviewedUsDayStrategyManifestError("strategy_manifest_lineage_invalid")
        return self


__all__ = (
    "InvalidReviewedUsDayStrategyManifestError",
    "ReviewedUsDayStrategyManifest",
)
