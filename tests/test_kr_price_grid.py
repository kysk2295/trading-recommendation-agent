from decimal import Decimal

import pytest

from trading_agent.kr_price_grid import (
    InvalidKrEquityPriceError,
    is_valid_kr_equity_price,
    kr_equity_tick_size,
    round_kr_equity_price_down,
    round_kr_equity_price_up,
)


@pytest.mark.parametrize(
    ("price", "expected"),
    (
        ("1999", "1"),
        ("2000", "5"),
        ("4999", "5"),
        ("5000", "10"),
        ("19999", "10"),
        ("20000", "50"),
        ("49999", "50"),
        ("50000", "100"),
        ("199999", "100"),
        ("200000", "500"),
        ("499999", "500"),
        ("500000", "1000"),
        ("999999", "1000"),
    ),
)
def test_tick_size_uses_official_equity_band_when_price_is_at_boundary(
    price: str,
    expected: str,
) -> None:
    # Given an ordinary-equity price on either side of a KRX band boundary
    value = Decimal(price)

    # When its tick size is requested
    tick = kr_equity_tick_size(value)

    # Then the current official equity band applies
    assert tick == Decimal(expected)


@pytest.mark.parametrize(
    ("price", "down", "up"),
    (
        ("1999.1", "1999", "2000"),
        ("4999", "4995", "5000"),
        ("19999", "19990", "20000"),
        ("49999", "49950", "50000"),
        ("199999", "199900", "200000"),
        ("499999", "499500", "500000"),
        ("500001", "500000", "501000"),
    ),
)
def test_normalization_crosses_band_with_conservative_valid_prices(
    price: str,
    down: str,
    up: str,
) -> None:
    # Given a positive price between valid KRX grid points
    value = Decimal(price)

    # When it is normalized in either conservative direction
    normalized = (round_kr_equity_price_down(value), round_kr_equity_price_up(value))

    # Then both results are valid and bracket the source price
    assert normalized == (Decimal(down), Decimal(up))
    assert all(is_valid_kr_equity_price(item) for item in normalized)


@pytest.mark.parametrize("price", (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")))
def test_price_grid_rejects_nonpositive_or_nonfinite_values(price: Decimal) -> None:
    # Given an invalid ordinary-equity price
    # When any public grid operation receives it
    operations = (
        kr_equity_tick_size,
        is_valid_kr_equity_price,
        round_kr_equity_price_down,
        round_kr_equity_price_up,
    )

    # Then it raises the typed boundary error
    for operation in operations:
        with pytest.raises(InvalidKrEquityPriceError):
            _ = operation(price)


def test_round_down_rejects_price_below_smallest_positive_grid_point() -> None:
    # Given a positive price below the smallest ordinary-equity grid point
    price = Decimal("0.1")

    # When conservative downward normalization has no positive result
    # Then it raises the typed price error instead of returning zero
    with pytest.raises(InvalidKrEquityPriceError):
        _ = round_kr_equity_price_down(price)
