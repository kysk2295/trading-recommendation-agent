from __future__ import annotations

from typing import Final

from trading_agent.research_identity_models import (
    AgentFamily,
    MarketId,
    StrategyLaneRef,
)

KR_THEME_OPPORTUNITY_LANE: Final = StrategyLaneRef(
    market_id=MarketId.KR_EQUITIES,
    agent_family=AgentFamily.OPPORTUNITY_MANAGER,
    strategy_id="theme_momentum",
)
