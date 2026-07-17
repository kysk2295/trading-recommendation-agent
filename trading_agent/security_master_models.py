from __future__ import annotations

import datetime as dt
import re
from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, override
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, model_validator

_OPAQUE_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_VENUE = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,31}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class DataMarketDomain(StrEnum):
    US_EQUITIES = "us_equities"
    KR_EQUITIES = "kr_equities"
    US_DERIVATIVES = "us_derivatives"
    GLOBAL_MACRO = "global_macro"
    RESEARCH_KNOWLEDGE = "research_knowledge"


class AssetClass(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    OPTION = "option"
    FUTURE = "future"
    INDEX = "index"
    MACRO_SERIES = "macro_series"


class InstrumentAliasType(StrEnum):
    SYMBOL = "symbol"
    PROVIDER_SYMBOL = "provider_symbol"
    ISIN = "isin"
    FIGI = "figi"
    CIK = "cik"
    CORP_CODE = "corp_code"


class CorporateActionType(StrEnum):
    SPLIT = "split"
    CASH_DIVIDEND = "cash_dividend"
    SYMBOL_CHANGE = "symbol_change"
    MERGER = "merger"
    SPIN_OFF = "spin_off"
    DELISTING = "delisting"


class InstrumentId(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    value: str
    market_domain: DataMarketDomain
    asset_class: AssetClass
    venue: str
    currency: str
    timezone: str
    valid_from: dt.datetime
    valid_to: dt.datetime | None = None

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        try:
            _ = ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("invalid instrument timezone") from None
        if (
            _OPAQUE_ID.fullmatch(self.value) is None
            or _VENUE.fullmatch(self.venue) is None
            or _CURRENCY.fullmatch(self.currency) is None
            or not _aware(self.valid_from)
            or (self.valid_to is not None and (not _aware(self.valid_to) or self.valid_to <= self.valid_from))
        ):
            raise ValueError("invalid instrument identity")
        return self


class InstrumentAlias(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    instrument_id: str
    namespace: str
    alias_type: InstrumentAliasType
    value: str
    effective_from: dt.datetime
    effective_to: dt.datetime | None = None

    @model_validator(mode="after")
    def validate_alias(self) -> Self:
        if (
            _OPAQUE_ID.fullmatch(self.instrument_id) is None
            or _NAMESPACE.fullmatch(self.namespace) is None
            or not _canonical_text(self.value, max_length=128)
            or not _aware(self.effective_from)
            or (
                self.effective_to is not None
                and (not _aware(self.effective_to) or self.effective_to <= self.effective_from)
            )
        ):
            raise ValueError("invalid instrument alias")
        return self

    @property
    def canonical_key(self) -> str:
        return ":".join(
            (
                self.namespace,
                self.alias_type.value,
                self.value,
                self.effective_from.isoformat(),
            )
        )


class CorporateAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    action_id: str
    action_type: CorporateActionType
    instrument_id: str
    announced_at: dt.datetime
    effective_at: dt.datetime
    ratio_numerator: Decimal | None = None
    ratio_denominator: Decimal | None = None
    cash_amount: Decimal | None = None
    currency: str | None = None
    successor_instrument_id: str | None = None

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        ratio_present = self.ratio_numerator is not None or self.ratio_denominator is not None
        ratio_valid = (
            self.ratio_numerator is not None
            and self.ratio_denominator is not None
            and _positive_finite(self.ratio_numerator)
            and _positive_finite(self.ratio_denominator)
        )
        cash_present = self.cash_amount is not None or self.currency is not None
        cash_valid = (
            self.cash_amount is not None
            and self.currency is not None
            and _positive_finite(self.cash_amount)
            and _CURRENCY.fullmatch(self.currency) is not None
        )
        successor_valid = (
            self.successor_instrument_id is not None
            and _OPAQUE_ID.fullmatch(self.successor_instrument_id) is not None
            and self.successor_instrument_id != self.instrument_id
        )
        common_valid = (
            _OPAQUE_ID.fullmatch(self.action_id) is not None
            and _OPAQUE_ID.fullmatch(self.instrument_id) is not None
            and _aware(self.announced_at)
            and _aware(self.effective_at)
            and self.effective_at >= self.announced_at
            and (not ratio_present or ratio_valid)
            and (not cash_present or cash_valid)
        )
        shape_valid = {
            CorporateActionType.SPLIT: ratio_valid and not cash_present and self.successor_instrument_id is None,
            CorporateActionType.CASH_DIVIDEND: (
                cash_valid and not ratio_present and self.successor_instrument_id is None
            ),
            CorporateActionType.SYMBOL_CHANGE: (
                not ratio_present and not cash_present and self.successor_instrument_id is None
            ),
            CorporateActionType.MERGER: successor_valid,
            CorporateActionType.SPIN_OFF: successor_valid,
            CorporateActionType.DELISTING: (
                not ratio_present and not cash_present and self.successor_instrument_id is None
            ),
        }[self.action_type]
        if not common_valid or not shape_valid:
            raise ValueError("invalid corporate action")
        return self


class InstrumentAliasResolutionError(ValueError):
    @override
    def __str__(self) -> str:
        return "종목 alias를 정확히 하나로 해석하지 못했습니다"


def resolve_instrument_alias(
    aliases: Sequence[InstrumentAlias],
    *,
    namespace: str,
    value: str,
    as_of: dt.datetime,
) -> str:
    if _NAMESPACE.fullmatch(namespace) is None or not _canonical_text(value, max_length=128) or not _aware(as_of):
        raise InstrumentAliasResolutionError
    matches = tuple(
        alias
        for alias in aliases
        if alias.namespace == namespace
        and alias.value == value
        and alias.effective_from <= as_of
        and (alias.effective_to is None or as_of < alias.effective_to)
    )
    if len(matches) != 1:
        raise InstrumentAliasResolutionError
    return matches[0].instrument_id


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonical_text(value: str, *, max_length: int) -> bool:
    return (
        bool(value)
        and value == value.strip()
        and len(value) <= max_length
        and not any(character in value for character in "\r\n\t")
    )


def _positive_finite(value: Decimal) -> bool:
    return value.is_finite() and value > 0


__all__ = (
    "AssetClass",
    "CorporateAction",
    "CorporateActionType",
    "DataMarketDomain",
    "InstrumentAlias",
    "InstrumentAliasResolutionError",
    "InstrumentAliasType",
    "InstrumentId",
    "resolve_instrument_alias",
)
