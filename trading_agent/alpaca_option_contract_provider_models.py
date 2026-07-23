from __future__ import annotations

import datetime as dt
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from trading_agent.alpaca_option_chain_models import OptionContractType


class OptionExerciseStyle(StrEnum):
    AMERICAN = "american"
    EUROPEAN = "european"


class ProviderOptionContractStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class ProviderOptionContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    symbol: str
    name: str
    status: ProviderOptionContractStatus
    tradable: bool
    expiration_date: dt.date
    root_symbol: str
    underlying_symbol: str
    underlying_asset_id: UUID
    type: OptionContractType
    style: OptionExerciseStyle
    strike_price: Decimal = Field(gt=0)
    size: Decimal = Field(gt=0)
    multiplier: Decimal = Field(gt=0)
    open_interest: int | None = Field(default=None, ge=0)
    open_interest_date: dt.date | None = None
    close_price: Decimal | None = Field(default=None, ge=0)
    close_price_date: dt.date | None = None
    ppind: bool | None = None

    @model_validator(mode="after")
    def validate_observations(self) -> Self:
        if (
            self.size != self.multiplier
            or (self.open_interest is None)
            != (self.open_interest_date is None)
            or (self.close_price is None)
            != (self.close_price_date is None)
        ):
            raise ProviderOptionContractError
        return self


class ProviderOptionContractPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    option_contracts: tuple[ProviderOptionContract, ...]
    page_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("page_token", "next_page_token"),
    )
    limit: int | None = Field(default=None, ge=1, le=10_000)

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        token = self.page_token
        if token is not None and (
            not 0 < len(token) <= 2_048
            or any(character < " " for character in token)
        ):
            raise ProviderOptionContractError
        return self


class ProviderOptionContractError(ValueError):
    def __str__(self) -> str:
        return "Alpaca provider option contract payload is invalid"


__all__ = (
    "OptionExerciseStyle",
    "ProviderOptionContract",
    "ProviderOptionContractPage",
    "ProviderOptionContractStatus",
)
