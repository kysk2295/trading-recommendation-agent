from __future__ import annotations

import datetime as dt
import json
import re
from decimal import Decimal
from typing import Literal, Self, override

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from trading_agent.kr_instrument import (
    is_kr_instrument_symbol_v1,
    is_kr_instrument_symbol_v2,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_KIS_SOURCE_RUN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,115}:kis_ranking$"
)


class InvalidKrVolumeSurgePayloadError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR volume surge payload가 유효하지 않습니다"


class KrVolumeSurgeSymbol(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    symbol: str
    trading_value_krw: Decimal
    volume_ratio: Decimal

    @model_validator(mode="after")
    def validate_symbol(self) -> Self:
        if (
            not is_kr_instrument_symbol_v1(self.symbol)
            or not _nonnegative_finite(self.trading_value_krw)
            or not _nonnegative_finite(self.volume_ratio)
        ):
            raise ValueError("invalid KR volume-surge symbol")
        return self


class KrVolumeSurgePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    observed_at: dt.datetime
    symbols: tuple[KrVolumeSurgeSymbol, ...]

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        symbols = tuple(item.symbol for item in self.symbols)
        if (
            not _aware(self.observed_at)
            or not self.symbols
            or symbols != tuple(sorted(set(symbols)))
        ):
            raise ValueError("invalid KR volume-surge payload")
        return self


class KrVolumeSurgeSymbolV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    symbol: str
    trading_value_krw: Decimal
    volume_ratio: Decimal
    source_catalyst_id: str

    @model_validator(mode="after")
    def validate_symbol(self) -> Self:
        if (
            not is_kr_instrument_symbol_v2(self.symbol)
            or not _nonnegative_finite(self.trading_value_krw)
            or not _nonnegative_finite(self.volume_ratio)
            or _SHA256.fullmatch(self.source_catalyst_id) is None
        ):
            raise ValueError("invalid KR volume-surge v2 symbol")
        return self


class KrVolumeSurgePayloadV2(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[2] = 2
    observed_at: dt.datetime
    source_observed_at: dt.datetime
    source_run_id: str
    symbols: tuple[KrVolumeSurgeSymbolV2, ...]

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        symbols = tuple(item.symbol for item in self.symbols)
        source_ids = tuple(item.source_catalyst_id for item in self.symbols)
        if (
            not _aware(self.observed_at)
            or not _aware(self.source_observed_at)
            or self.source_observed_at > self.observed_at
            or _KIS_SOURCE_RUN.fullmatch(self.source_run_id) is None
            or len(self.source_run_id) > 128
            or symbols != tuple(sorted(set(symbols)))
            or len(source_ids) != len(set(source_ids))
        ):
            raise ValueError("invalid KR volume-surge v2 payload")
        return self


type KrVolumeSurgePayloadAny = KrVolumeSurgePayload | KrVolumeSurgePayloadV2


def canonical_kr_volume_surge_payload(payload: KrVolumeSurgePayloadAny) -> bytes:
    return json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def parse_kr_volume_surge_payload(raw_payload: bytes) -> KrVolumeSurgePayloadAny:
    try:
        document: object = json.loads(raw_payload)
        if not isinstance(document, dict):
            raise ValueError
        version = document.get("schema_version", 1)
        if type(version) is not int:
            raise ValueError
        if version == 1:
            return KrVolumeSurgePayload.model_validate(document)
        if version == 2:
            return KrVolumeSurgePayloadV2.model_validate(document)
        raise ValueError
    except (UnicodeError, json.JSONDecodeError, ValidationError, ValueError, TypeError):
        raise InvalidKrVolumeSurgePayloadError from None


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _nonnegative_finite(value: Decimal) -> bool:
    return value.is_finite() and value >= 0
