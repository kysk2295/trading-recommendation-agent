from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.us_day_situation_models import UsDaySituationMap
from trading_agent.us_day_thesis_models import UsDayCurrentMarket


class CanonicalUsDaySource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    situation: UsDaySituationMap
    current_markets: tuple[UsDayCurrentMarket, ...]

    @model_validator(mode="after")
    def require_market_lineage(self) -> Self:
        leaders = {leader.symbol for theme in self.situation.themes for leader in theme.leaders}
        if {item.symbol for item in self.current_markets} != leaders or len(self.current_markets) != len(leaders):
            raise ValueError("canonical_market_lineage_invalid")
        return self


__all__ = ("CanonicalUsDaySource",)
