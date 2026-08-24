from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Final, override

KRX_EQUITY_PRICE_GRID_SOURCE_URL: Final = "https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T3.jsp"
KRX_EQUITY_PRICE_GRID_RULESET_VERSION: Final = "krx-equity-price-grid-2026-08-24"

_TWO_THOUSAND: Final = Decimal(2_000)
_FIVE_THOUSAND: Final = Decimal(5_000)
_TWENTY_THOUSAND: Final = Decimal(20_000)
_FIFTY_THOUSAND: Final = Decimal(50_000)
_TWO_HUNDRED_THOUSAND: Final = Decimal(200_000)
_FIVE_HUNDRED_THOUSAND: Final = Decimal(500_000)


class InvalidKrEquityPriceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR ordinary-equity price must be a positive finite Decimal"


def kr_equity_tick_size(price: Decimal) -> Decimal:
    _require_price(price)
    if price < _TWO_THOUSAND:
        return Decimal(1)
    if price < _FIVE_THOUSAND:
        return Decimal(5)
    if price < _TWENTY_THOUSAND:
        return Decimal(10)
    if price < _FIFTY_THOUSAND:
        return Decimal(50)
    if price < _TWO_HUNDRED_THOUSAND:
        return Decimal(100)
    if price < _FIVE_HUNDRED_THOUSAND:
        return Decimal(500)
    return Decimal(1_000)


def is_valid_kr_equity_price(price: Decimal) -> bool:
    tick = kr_equity_tick_size(price)
    return price % tick == 0


def round_kr_equity_price_down(price: Decimal) -> Decimal:
    tick = kr_equity_tick_size(price)
    rounded = (price / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
    if rounded <= 0:
        raise InvalidKrEquityPriceError
    return rounded


def round_kr_equity_price_up(price: Decimal) -> Decimal:
    tick = kr_equity_tick_size(price)
    return (price / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _require_price(price: Decimal) -> None:
    if type(price) is not Decimal or not price.is_finite() or price <= 0:
        raise InvalidKrEquityPriceError
