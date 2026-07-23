from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from trading_agent.treasury_yield_models import (
    TreasuryMaturity,
    TreasuryYieldContext,
    TreasuryYieldError,
    TreasuryYieldPoint,
    TreasuryYieldRawResponse,
    TreasuryYieldRequest,
)

_ATOM = "{http://www.w3.org/2005/Atom}"
_DATA = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
_META = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"
_FIELDS = (
    ("BC_1MONTH", TreasuryMaturity.ONE_MONTH),
    ("BC_1_5MONTH", TreasuryMaturity.ONE_AND_HALF_MONTH),
    ("BC_2MONTH", TreasuryMaturity.TWO_MONTH),
    ("BC_3MONTH", TreasuryMaturity.THREE_MONTH),
    ("BC_4MONTH", TreasuryMaturity.FOUR_MONTH),
    ("BC_6MONTH", TreasuryMaturity.SIX_MONTH),
    ("BC_1YEAR", TreasuryMaturity.ONE_YEAR),
    ("BC_2YEAR", TreasuryMaturity.TWO_YEAR),
    ("BC_3YEAR", TreasuryMaturity.THREE_YEAR),
    ("BC_5YEAR", TreasuryMaturity.FIVE_YEAR),
    ("BC_7YEAR", TreasuryMaturity.SEVEN_YEAR),
    ("BC_10YEAR", TreasuryMaturity.TEN_YEAR),
    ("BC_20YEAR", TreasuryMaturity.TWENTY_YEAR),
    ("BC_30YEAR", TreasuryMaturity.THIRTY_YEAR),
)
_REQUIRED = frozenset(("Id", "NEW_DATE", *(field for field, _ in _FIELDS)))
_ALLOWED = _REQUIRED | {"BC_30YEARDISPLAY"}


@dataclass(frozen=True, slots=True)
class _Curve:
    date: dt.date
    values: tuple[Decimal, ...]


def parse_treasury_yield_context(
    request: TreasuryYieldRequest,
    response: TreasuryYieldRawResponse,
) -> TreasuryYieldContext:
    try:
        if (
            response.request_id != request.request_id
            or response.status_code != 200
            or response.content_type not in {"application/xml", "text/xml"}
            or b"<!DOCTYPE" in response.raw_payload.upper()
            or b"<!ENTITY" in response.raw_payload.upper()
        ):
            raise TreasuryYieldError
        root = ET.fromstring(response.raw_payload)
        if root.tag != f"{_ATOM}feed":
            raise TreasuryYieldError
        curves = tuple(_parse_entry(entry, request) for entry in root.findall(f"{_ATOM}entry"))
        dates = tuple(curve.date for curve in curves)
        if len(curves) < 2 or len(dates) != len(set(dates)):
            raise TreasuryYieldError
        latest, previous = sorted(
            (curve for curve in curves if curve.date <= request.through_date),
            key=lambda curve: curve.date,
            reverse=True,
        )[:2]
        points = tuple(
            TreasuryYieldPoint(
                maturity=maturity,
                current_percent=current,
                previous_percent=prior,
                change_bps=(current - prior) * 100,
            )
            for (_, maturity), current, prior in zip(
                _FIELDS,
                latest.values,
                previous.values,
                strict=True,
            )
        )
        current = {point.maturity: point.current_percent for point in points}
        return TreasuryYieldContext(
            request_id=request.request_id,
            raw_receipt_id=response.receipt_id,
            latest_date=latest.date,
            previous_date=previous.date,
            observed_at=response.received_at,
            points=points,
            ten_year_minus_two_year_bps=(current[TreasuryMaturity.TEN_YEAR] - current[TreasuryMaturity.TWO_YEAR]) * 100,
            ten_year_minus_three_month_bps=(current[TreasuryMaturity.TEN_YEAR] - current[TreasuryMaturity.THREE_MONTH])
            * 100,
            thirty_year_minus_five_year_bps=(
                current[TreasuryMaturity.THIRTY_YEAR] - current[TreasuryMaturity.FIVE_YEAR]
            )
            * 100,
        )
    except TreasuryYieldError:
        raise
    except (
        ET.ParseError,
        IndexError,
        InvalidOperation,
        TypeError,
        ValidationError,
        ValueError,
    ):
        raise TreasuryYieldError from None


def _parse_entry(
    entry: ET.Element,
    request: TreasuryYieldRequest,
) -> _Curve:
    contents = entry.findall(f"{_ATOM}content")
    if len(contents) != 1:
        raise TreasuryYieldError
    properties = contents[0].findall(f"{_META}properties")
    if len(properties) != 1:
        raise TreasuryYieldError
    payload: dict[str, str] = {}
    for child in properties[0]:
        if not child.tag.startswith(_DATA) or len(child) or child.text is None:
            raise TreasuryYieldError
        key = child.tag.removeprefix(_DATA)
        if key in payload or key not in _ALLOWED:
            raise TreasuryYieldError
        payload[key] = child.text
    if not _REQUIRED.issubset(payload):
        raise TreasuryYieldError
    if int(payload["Id"]) < 1:
        raise TreasuryYieldError
    timestamp = dt.datetime.fromisoformat(payload["NEW_DATE"])
    if timestamp.tzinfo is not None or timestamp.time() != dt.time():
        raise TreasuryYieldError
    date = timestamp.date()
    if (date.year, date.month) != (
        request.through_date.year,
        request.through_date.month,
    ):
        raise TreasuryYieldError
    values = tuple(_yield(payload[field]) for field, _ in _FIELDS)
    display = payload.get("BC_30YEARDISPLAY")
    if display is not None and _yield(display) != values[-1]:
        raise TreasuryYieldError
    return _Curve(date=date, values=values)


def _yield(value: str) -> Decimal:
    parsed = Decimal(value)
    if not parsed.is_finite() or not Decimal("-5") <= parsed <= Decimal("25"):
        raise TreasuryYieldError
    return parsed


__all__ = ("parse_treasury_yield_context",)
