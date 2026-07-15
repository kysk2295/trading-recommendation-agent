from __future__ import annotations

from typing import TYPE_CHECKING

from trading_agent.models import BarInput, Recommendation, RecommendationState

if TYPE_CHECKING:
    from trading_agent.engine import RecommendationEngine

__all__ = (
    "BarInput",
    "Recommendation",
    "RecommendationEngine",
    "RecommendationState",
)


def __getattr__(name: str) -> object:
    if name == "RecommendationEngine":
        from trading_agent.engine import RecommendationEngine

        return RecommendationEngine
    raise AttributeError(name)
