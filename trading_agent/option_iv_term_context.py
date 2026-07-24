from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.alpaca_option_chain_models import (
    OptionContractType,
    OptionFeed,
)
from trading_agent.alpaca_option_term_structure_models import (
    AlpacaOptionTermStructure,
    OptionTermStructureStatus,
)
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.private_immutable_file import (
    InvalidPrivateImmutableFileError,
    publish_private_immutable_text,
)
from trading_agent.private_query_bytes import (
    InvalidPrivateQueryBytesError,
    read_private_bytes_query_only,
)

_MAX_SOURCE_BYTES = 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class OptionIvTermState(StrEnum):
    BACK_PREMIUM = "back_premium"
    FLAT = "flat"
    FRONT_PREMIUM = "front_premium"


class OptionIvTermContextError(ValueError):
    @override
    def __str__(self) -> str:
        return "option IV term context is invalid"


class OptionIvTermContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_term_structure_id: str
    source_sha256: str
    feed: OptionFeed
    underlying_symbol: str
    market_date: dt.date
    as_of: dt.datetime
    contract_type: OptionContractType
    source_expiration_count: int
    near_expiration_date: dt.date
    far_expiration_date: dt.date
    near_days_to_expiry: int
    far_days_to_expiry: int
    near_median_implied_volatility: Decimal
    far_median_implied_volatility: Decimal
    front_minus_back_iv: Decimal
    state: OptionIvTermState

    @model_validator(mode="after")
    def validate_context(self) -> Self:
        difference = (
            self.near_median_implied_volatility
            - self.far_median_implied_volatility
        )
        expected_state = (
            OptionIvTermState.FRONT_PREMIUM
            if difference > 0
            else OptionIvTermState.BACK_PREMIUM
            if difference < 0
            else OptionIvTermState.FLAT
        )
        if (
            _SHA256.fullmatch(self.source_term_structure_id) is None
            or _SHA256.fullmatch(self.source_sha256) is None
            or not self.underlying_symbol
            or not _aware(self.as_of)
            or not 2 <= self.source_expiration_count <= 32
            or self.near_expiration_date >= self.far_expiration_date
            or not 1 <= self.near_days_to_expiry < self.far_days_to_expiry
            or self.near_median_implied_volatility <= 0
            or self.far_median_implied_volatility <= 0
            or self.front_minus_back_iv != difference
            or self.state is not expected_state
        ):
            raise OptionIvTermContextError
        return self

    @property
    def context_id(self) -> str:
        return hashlib.sha256(
            canonical_experiment_ledger_json(self).encode()
        ).hexdigest()


def build_option_iv_term_context(source_path: Path) -> OptionIvTermContext:
    try:
        payload = read_private_bytes_query_only(
            source_path,
            max_bytes=_MAX_SOURCE_BYTES,
        )
        structure = AlpacaOptionTermStructure.model_validate_json(payload)
        if (
            source_path.name
            != f"option_term_structure_{structure.term_structure_id}.json"
            or structure.status is not OptionTermStructureStatus.READY
            or structure.surface_count != structure.expiration_count
            or len({item.contract_type for item in structure.slices}) != 1
        ):
            raise OptionIvTermContextError
        slices = tuple(
            sorted(structure.slices, key=lambda item: item.expiration_date)
        )
        near = slices[0]
        far = slices[-1]
        difference = (
            near.median_implied_volatility - far.median_implied_volatility
        )
        state = (
            OptionIvTermState.FRONT_PREMIUM
            if difference > 0
            else OptionIvTermState.BACK_PREMIUM
            if difference < 0
            else OptionIvTermState.FLAT
        )
        return OptionIvTermContext(
            source_term_structure_id=structure.term_structure_id,
            source_sha256=hashlib.sha256(payload).hexdigest(),
            feed=structure.feed,
            underlying_symbol=structure.underlying_symbol,
            market_date=structure.market_date,
            as_of=structure.as_of,
            contract_type=near.contract_type,
            source_expiration_count=structure.expiration_count,
            near_expiration_date=near.expiration_date,
            far_expiration_date=far.expiration_date,
            near_days_to_expiry=near.days_to_expiry,
            far_days_to_expiry=far.days_to_expiry,
            near_median_implied_volatility=near.median_implied_volatility,
            far_median_implied_volatility=far.median_implied_volatility,
            front_minus_back_iv=difference,
            state=state,
        )
    except OptionIvTermContextError:
        raise
    except (
        IndexError,
        InvalidPrivateQueryBytesError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise OptionIvTermContextError from None


def publish_option_iv_term_context(
    output_root: Path,
    context: OptionIvTermContext,
) -> tuple[Path, bool]:
    try:
        checked = OptionIvTermContext.model_validate(context.model_dump())
        path = output_root / f"option_iv_term_context_{checked.context_id}.json"
        created = publish_private_immutable_text(
            path,
            canonical_experiment_ledger_json(checked) + "\n",
        )
        return path, created
    except OptionIvTermContextError:
        raise
    except (
        InvalidPrivateImmutableFileError,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise OptionIvTermContextError from None


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "OptionIvTermContext",
    "OptionIvTermContextError",
    "OptionIvTermState",
    "build_option_iv_term_context",
    "publish_option_iv_term_context",
)
