from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading_agent.kis_kr_market_models import (
    KisKrMarketEvidenceError,
    KisKrMarketReceipt,
    KisKrMinuteEnvelope,
    KisKrMinuteRow,
    KisKrOrderBookEnvelope,
    KisKrPriceStatusEnvelope,
)

SEOUL: Final = ZoneInfo("Asia/Seoul")
_SESSION_OPEN: Final = dt.time(9)
_SESSION_CLOSE: Final = dt.time(15, 30)


def parse_minute_envelope(receipt: KisKrMarketReceipt) -> KisKrMinuteEnvelope:
    try:
        envelope = KisKrMinuteEnvelope.model_validate_json(receipt.raw_payload)
    except (ValidationError, ValueError):
        raise KisKrMarketEvidenceError from None
    if receipt.status_code != 200 or envelope.rt_cd != "0" or not envelope.output2:
        raise KisKrMarketEvidenceError
    return envelope


def parse_price_envelope(receipt: KisKrMarketReceipt) -> KisKrPriceStatusEnvelope:
    try:
        envelope = KisKrPriceStatusEnvelope.model_validate_json(receipt.raw_payload)
    except (ValidationError, ValueError):
        raise KisKrMarketEvidenceError from None
    if receipt.status_code != 200 or envelope.rt_cd != "0":
        raise KisKrMarketEvidenceError
    return envelope


def parse_quote_envelope(receipt: KisKrMarketReceipt) -> KisKrOrderBookEnvelope:
    try:
        envelope = KisKrOrderBookEnvelope.model_validate_json(receipt.raw_payload)
    except (ValidationError, ValueError):
        raise KisKrMarketEvidenceError from None
    if receipt.status_code != 200 or envelope.rt_cd != "0":
        raise KisKrMarketEvidenceError
    return envelope


def parse_bar_start(row: KisKrMinuteRow) -> dt.datetime:
    try:
        started_at = dt.datetime.strptime(
            f"{row.stck_bsop_date}{row.stck_cntg_hour}",
            "%Y%m%d%H%M%S",
        ).replace(tzinfo=SEOUL)
    except ValueError:
        raise KisKrMarketEvidenceError from None
    local_time = started_at.time()
    if started_at.second != 0 or not _SESSION_OPEN <= local_time < _SESSION_CLOSE:
        raise KisKrMarketEvidenceError
    return started_at


def parse_quote_time(receipt: KisKrMarketReceipt, hour: str) -> dt.datetime:
    try:
        local_date = receipt.received_at.astimezone(SEOUL).date()
        local_time = dt.datetime.strptime(hour, "%H%M%S").time()
    except ValueError:
        raise KisKrMarketEvidenceError from None
    return dt.datetime.combine(local_date, local_time, tzinfo=SEOUL)


def decimal_value(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise KisKrMarketEvidenceError from None
    if not result.is_finite():
        raise KisKrMarketEvidenceError
    return result


def positive_int(value: str) -> int:
    if not value.isdigit() or int(value) <= 0:
        raise KisKrMarketEvidenceError
    return int(value)


def optional_price(value: str) -> Decimal | None:
    price = decimal_value(value)
    return None if price == 0 else price
