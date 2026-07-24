from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal, InvalidOperation
from typing import cast

from trading_agent.kis_overseas_futures_models import (
    KisFuturesQuote,
    KisFuturesQuoteError,
    KisFuturesQuoteFailure,
    KisFuturesQuoteRawResponse,
    KisFuturesQuoteRequest,
)


def parse_kis_overseas_futures_quote(
    request: KisFuturesQuoteRequest,
    response: KisFuturesQuoteRawResponse,
) -> KisFuturesQuote:
    try:
        if (
            response.request_id != request.request_id
            or response.symbol not in request.symbols
            or response.content_type != "application/json"
        ):
            raise KisFuturesQuoteError(
                KisFuturesQuoteFailure.RESPONSE_STRUCTURE
            )
        payload = json.loads(response.raw_payload)
        if not isinstance(payload, dict):
            raise ValueError
        if payload.get("rt_cd") != "0":
            raise KisFuturesQuoteError(
                KisFuturesQuoteFailure.PROVIDER_STATUS
            )
        output = payload.get("output1")
        if not isinstance(output, dict):
            raise ValueError
        values = cast(dict[str, object], output)
        return KisFuturesQuote(
            symbol=response.symbol,
            exchange=_text(values, "exch_cd"),
            currency=_text(values, "crc_cd"),
            received_at=response.received_at,
            provider_process_date=_date(values, "proc_date"),
            provider_process_time=_time(values, "proc_time"),
            business_date=_date(values, "sbsnsdate"),
            listing_date=_date(values, "trd_fr_date"),
            expiration_date=_date(values, "expr_date"),
            last_trade_date=_date(values, "trd_to_date"),
            last_price=_decimal(values, "last_price"),
            bid_price=_decimal(values, "bid_price"),
            ask_price=_decimal(values, "ask_price"),
            previous_close=_decimal(values, "prev_price"),
            settlement_price=_optional_decimal(values, "sttl_price"),
            accumulated_volume=_non_negative_int(values, "vol"),
        )
    except KisFuturesQuoteError:
        raise
    except (
        InvalidOperation,
        json.JSONDecodeError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        raise KisFuturesQuoteError(
            KisFuturesQuoteFailure.RESPONSE_STRUCTURE
        ) from None


def _text(values: dict[str, object], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ValueError
    return value


def _date(values: dict[str, object], key: str) -> dt.date:
    return dt.datetime.strptime(_text(values, key), "%Y%m%d").date()


def _time(values: dict[str, object], key: str) -> dt.time:
    return dt.datetime.strptime(_text(values, key), "%H%M%S").time()


def _decimal(values: dict[str, object], key: str) -> Decimal:
    value = Decimal(_text(values, key))
    if not value.is_finite():
        raise ValueError
    return value


def _optional_decimal(
    values: dict[str, object],
    key: str,
) -> Decimal | None:
    value = values.get(key)
    if value in (None, "", "0", "0.0", "0.00"):
        return None
    return _decimal(values, key)


def _non_negative_int(values: dict[str, object], key: str) -> int:
    value = _text(values, key)
    if not value.isdigit():
        raise ValueError
    return int(value)


__all__ = ("parse_kis_overseas_futures_quote",)
