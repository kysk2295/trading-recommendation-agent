from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import FeatureValue, SourceCoverage

_OPAQUE_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_REGIME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_LANE_PART = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")


class MarketContextContractError(ValueError):
    pass


class MarketRegimeLabel(StrEnum):
    """Coarse regime tags. Not a trading signal."""

    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    HIGH_VOL = "high_vol"
    LOW_VOL = "low_vol"
    TRENDING = "trending"
    MEAN_REVERTING = "mean_reverting"
    ILLIQUID = "illiquid"
    UNKNOWN = "unknown"


class MarketContextSnapshot(BaseModel):
    """Independent market-regime snapshot (design §8.2). Shadow/research only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    context_id: str
    market_id: MarketId
    observed_at: dt.datetime
    valid_until: dt.datetime
    regime_labels: tuple[MarketRegimeLabel, ...] = Field(min_length=1, max_length=8)
    breadth_and_volatility_features: tuple[FeatureValue, ...] = Field(max_length=64)
    macro_and_flow_refs: tuple[str, ...] = Field(max_length=32)
    coverage: tuple[SourceCoverage, ...] = Field(min_length=1, max_length=16)
    producer_version: str
    order_authority: Literal[False] = False
    allocation_authority: Literal[False] = False
    lifecycle_authority: Literal[False] = False

    @field_validator("observed_at", "valid_until")
    @classmethod
    def normalize_time(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise MarketContextContractError("invalid market context snapshot")
        return value.astimezone(dt.UTC)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        labels = tuple(item.value for item in self.regime_labels)
        feature_names = tuple(item.name for item in self.breadth_and_volatility_features)
        coverage_ids = tuple(item.source_id for item in self.coverage)
        if (
            _OPAQUE_ID.fullmatch(self.context_id) is None
            or self.valid_until <= self.observed_at
            or labels != tuple(sorted(set(labels)))
            or (
                MarketRegimeLabel.UNKNOWN in self.regime_labels
                and len(self.regime_labels) > 1
            )
            or feature_names != tuple(sorted(set(feature_names)))
            or self.macro_and_flow_refs != tuple(sorted(set(self.macro_and_flow_refs)))
            or any(_OPAQUE_ID.fullmatch(item) is None for item in self.macro_and_flow_refs)
            or coverage_ids != tuple(sorted(set(coverage_ids)))
            or any(item.observed_at > self.observed_at for item in self.coverage)
            or _VERSION.fullmatch(self.producer_version) is None
            or self.order_authority is not False
            or self.allocation_authority is not False
            or self.lifecycle_authority is not False
        ):
            raise MarketContextContractError("invalid market context snapshot")
        return self


class MarketContextBindingRule(BaseModel):
    """Strategies must preregister exact context producer version before use."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    strategy_lane_canonical_id: str
    required_context_producer_version: str
    max_context_age_seconds: int = Field(ge=1, le=86_400)
    allow_unknown_regime: bool = False

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        parts = self.strategy_lane_canonical_id.split("/")
        if (
            len(parts) != 3
            or any(_LANE_PART.fullmatch(part) is None for part in parts)
            or _VERSION.fullmatch(self.required_context_producer_version) is None
        ):
            raise MarketContextContractError("invalid market context binding rule")
        return self


def context_is_usable(
    snapshot: MarketContextSnapshot,
    rule: MarketContextBindingRule,
    *,
    as_of: dt.datetime,
) -> bool:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        return False
    age = (as_of - snapshot.observed_at).total_seconds()
    if (
        snapshot.producer_version != rule.required_context_producer_version
        or as_of > snapshot.valid_until
        or age < 0
        or age > rule.max_context_age_seconds
        or snapshot.order_authority
        or snapshot.allocation_authority
        or snapshot.lifecycle_authority
    ):
        return False
    return not (
        (not rule.allow_unknown_regime)
        and MarketRegimeLabel.UNKNOWN in snapshot.regime_labels
    )


__all__ = (
    "MarketContextBindingRule",
    "MarketContextContractError",
    "MarketContextSnapshot",
    "MarketRegimeLabel",
    "context_is_usable",
)
