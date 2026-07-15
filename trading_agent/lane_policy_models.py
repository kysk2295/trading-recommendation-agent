from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.paper_risk import (
    HARD_DAILY_LOSS_LIMIT_DOLLARS,
    HARD_MAX_NOTIONAL_DOLLARS,
    HARD_MAX_OPEN_POSITIONS,
    HARD_MAX_RISK_DOLLARS,
    HARD_MIN_PER_SIDE_COST_BPS,
    HARD_REFERENCE_EQUITY,
    HARD_RISK_FRACTION,
)


class LaneId(StrEnum):
    INTRADAY_MOMENTUM = "intraday_momentum"
    SWING_MOMENTUM = "swing_momentum"
    MARKET_REGIME = "market_regime"


class LaneOrderAuthority(StrEnum):
    ALPACA_PAPER = "alpaca_paper"
    SHADOW_ONLY = "shadow_only"
    NONE = "none"


class LaneRiskEnforcement(StrEnum):
    BROKER_PAPER = "broker_paper"
    SHADOW = "shadow"
    NONE = "none"


class IntradayExecutionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_kind: Literal["intraday_regular_session"] = "intraday_regular_session"
    policy_version: Literal[1] = 1
    lane_id: Literal[LaneId.INTRADAY_MOMENTUM] = LaneId.INTRADAY_MOMENTUM
    order_authority: Literal[LaneOrderAuthority.ALPACA_PAPER] = LaneOrderAuthority.ALPACA_PAPER
    state_machine: Literal["intraday_flat_by_close_v1"] = "intraday_flat_by_close_v1"
    entry_cutoff_before_close_minutes: Literal[30] = 30
    flatten_before_close_minutes: Literal[5] = 5
    allowed_session: Literal["regular"] = "regular"

    @property
    def order_states(self) -> tuple[str, ...]:
        return (
            "flat",
            "entry_pending",
            "partially_filled",
            "protected",
            "exit_pending",
            "closed",
        )


class SwingExecutionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_kind: Literal["swing_shadow_multisession"] = "swing_shadow_multisession"
    policy_version: Literal[1] = 1
    lane_id: Literal[LaneId.SWING_MOMENTUM] = LaneId.SWING_MOMENTUM
    order_authority: Literal[LaneOrderAuthority.SHADOW_ONLY] = LaneOrderAuthority.SHADOW_ONLY
    state_machine: Literal["swing_shadow_multisession_v1"] = "swing_shadow_multisession_v1"
    review_cadence: Literal["daily_close"] = "daily_close"

    @property
    def position_states(self) -> tuple[str, ...]:
        return (
            "flat",
            "entry_pending",
            "open_multisession",
            "exit_pending",
            "closed",
        )


class RegimeSignalExecutionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_kind: Literal["regime_signal_publish"] = "regime_signal_publish"
    policy_version: Literal[1] = 1
    lane_id: Literal[LaneId.MARKET_REGIME] = LaneId.MARKET_REGIME
    order_authority: Literal[LaneOrderAuthority.NONE] = LaneOrderAuthority.NONE
    state_machine: Literal["regime_signal_publish_v1"] = "regime_signal_publish_v1"
    publication_cadence: Literal["preopen_and_close"] = "preopen_and_close"

    @property
    def order_states(self) -> tuple[()]:
        return ()


LaneExecutionPolicy = Annotated[
    IntradayExecutionPolicy | SwingExecutionPolicy | RegimeSignalExecutionPolicy,
    Field(discriminator="policy_kind"),
]


class LaneRiskContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal[1] = 1
    enforcement: LaneRiskEnforcement
    reference_equity: Decimal
    risk_fraction: Decimal
    max_notional_dollars: Decimal
    max_planned_risk_dollars: Decimal
    max_open_positions: int
    daily_loss_limit_dollars: Decimal
    per_side_cost_bps: Decimal

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        decimal_values = (
            self.reference_equity,
            self.risk_fraction,
            self.max_notional_dollars,
            self.max_planned_risk_dollars,
            self.daily_loss_limit_dollars,
            self.per_side_cost_bps,
        )
        if not all(value.is_finite() and value >= 0 for value in decimal_values) or self.max_open_positions < 0:
            raise ValueError("lane risk limits must be finite and non-negative")
        if self.enforcement is LaneRiskEnforcement.NONE:
            if any(value != 0 for value in decimal_values) or self.max_open_positions != 0:
                raise ValueError("non-executing lanes must have zero risk capacity")
            return self
        if (
            not 0 < self.reference_equity <= Decimal(str(HARD_REFERENCE_EQUITY))
            or not 0 < self.risk_fraction <= Decimal(str(HARD_RISK_FRACTION))
            or not 0 < self.max_notional_dollars <= Decimal(str(HARD_MAX_NOTIONAL_DOLLARS))
            or not 0 < self.max_planned_risk_dollars <= Decimal(str(HARD_MAX_RISK_DOLLARS))
            or not 0 < self.max_open_positions <= HARD_MAX_OPEN_POSITIONS
            or not 0 < self.daily_loss_limit_dollars <= Decimal(str(HARD_DAILY_LOSS_LIMIT_DOLLARS))
            or self.per_side_cost_bps < Decimal(str(HARD_MIN_PER_SIDE_COST_BPS))
        ):
            raise ValueError("lane risk contract exceeds the approved Paper hard limits")
        return self
