from __future__ import annotations

from enum import StrEnum


class LaneId(StrEnum):
    INTRADAY_MOMENTUM = "intraday_momentum"
    SWING_MOMENTUM = "swing_momentum"
    MARKET_REGIME = "market_regime"


__all__ = ("LaneId",)
