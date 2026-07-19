from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self, override

from pydantic import BaseModel, ConfigDict, StrictStr, model_validator

from trading_agent.kr_instrument import is_kr_instrument_symbol_v2


class KisKrMarketEvidenceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR market evidence is invalid"


class KisKrMarketReceiptKind(StrEnum):
    MINUTE_BARS = "minute_bars"
    PRICE_STATUS = "price_status"
    ORDER_BOOK = "order_book"


@dataclass(frozen=True, slots=True)
class KisKrMarketReceipt:
    kind: KisKrMarketReceiptKind
    symbol: str
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.kind) is not KisKrMarketReceiptKind
            or not is_kr_instrument_symbol_v2(self.symbol)
            or not _aware(self.received_at)
            or type(self.status_code) is not int
            or not 100 <= self.status_code <= 599
            or self.content_type != "application/json"
            or type(self.raw_payload) is not bytes
            or not self.raw_payload
        ):
            raise KisKrMarketEvidenceError

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(self.raw_payload).hexdigest()


class KisKrMinuteProjectionInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    receipts: tuple[KisKrMarketReceipt, ...]
    evaluated_at: dt.datetime

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        symbols = {receipt.symbol for receipt in self.receipts}
        if (
            not self.receipts
            or len(symbols) != 1
            or not _aware(self.evaluated_at)
            or any(
                receipt.kind is not KisKrMarketReceiptKind.MINUTE_BARS or receipt.received_at > self.evaluated_at
                for receipt in self.receipts
            )
        ):
            raise KisKrMarketEvidenceError
        return self


class KisKrSnapshotProjectionInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    price_receipt: KisKrMarketReceipt
    quote_receipt: KisKrMarketReceipt
    evaluated_at: dt.datetime

    @model_validator(mode="after")
    def validate_input(self) -> Self:
        if (
            self.price_receipt.kind is not KisKrMarketReceiptKind.PRICE_STATUS
            or self.quote_receipt.kind is not KisKrMarketReceiptKind.ORDER_BOOK
            or self.price_receipt.symbol != self.quote_receipt.symbol
            or not _aware(self.evaluated_at)
            or self.price_receipt.received_at > self.evaluated_at
            or self.quote_receipt.received_at > self.evaluated_at
        ):
            raise KisKrMarketEvidenceError
        return self


_PROVIDER_CONFIG = ConfigDict(frozen=True, extra="allow")
_ENVELOPE_CONFIG = ConfigDict(frozen=True, extra="forbid")


class KisKrMinuteRow(BaseModel):
    model_config = _PROVIDER_CONFIG

    stck_bsop_date: StrictStr
    stck_cntg_hour: StrictStr
    stck_prpr: StrictStr
    stck_oprc: StrictStr
    stck_hgpr: StrictStr
    stck_lwpr: StrictStr
    cntg_vol: StrictStr
    acml_tr_pbmn: StrictStr


class _MinuteSummary(BaseModel):
    model_config = _PROVIDER_CONFIG


class KisKrMinuteEnvelope(BaseModel):
    model_config = _ENVELOPE_CONFIG

    rt_cd: StrictStr
    msg_cd: StrictStr
    msg1: StrictStr
    output1: _MinuteSummary
    output2: tuple[KisKrMinuteRow, ...]


class KisKrPriceStatusRow(BaseModel):
    model_config = _PROVIDER_CONFIG

    stck_shrn_iscd: StrictStr
    stck_prpr: StrictStr
    stck_sdpr: StrictStr
    stck_mxpr: StrictStr
    stck_llam: StrictStr
    temp_stop_yn: StrictStr
    vi_cls_code: StrictStr
    invt_caful_yn: StrictStr
    mrkt_warn_cls_code: StrictStr
    short_over_yn: StrictStr
    sltr_yn: StrictStr
    mang_issu_cls_code: StrictStr


class KisKrPriceStatusEnvelope(BaseModel):
    model_config = _ENVELOPE_CONFIG

    rt_cd: StrictStr
    msg_cd: StrictStr
    msg1: StrictStr
    output: KisKrPriceStatusRow


class KisKrOrderBookRow(BaseModel):
    model_config = _PROVIDER_CONFIG

    aspr_acpt_hour: StrictStr
    askp1: StrictStr
    bidp1: StrictStr


class KisKrOrderBookStatusRow(BaseModel):
    model_config = _PROVIDER_CONFIG

    stck_shrn_iscd: StrictStr
    new_mkop_cls_code: StrictStr
    antc_mkop_cls_code: StrictStr
    stck_prpr: StrictStr
    stck_sdpr: StrictStr
    vi_cls_code: StrictStr


class KisKrOrderBookEnvelope(BaseModel):
    model_config = _ENVELOPE_CONFIG

    rt_cd: StrictStr
    msg_cd: StrictStr
    msg1: StrictStr
    output1: KisKrOrderBookRow
    output2: KisKrOrderBookStatusRow


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
