from __future__ import annotations

from decimal import Decimal
from typing import assert_never

from trading_agent.alpaca_option_chain_models import OptionContractType
from trading_agent.alpaca_option_skew_models import (
    DELTA_BUCKET_RANGES,
    STRIKE_BUCKET_RANGES,
    AlpacaOptionSkewError,
    DeltaSkewBucket,
    StrikeSkewBucket,
)
from trading_agent.alpaca_option_surface import AlpacaOptionSurface


def build_strike_skew_buckets(
    call: AlpacaOptionSurface,
    put: AlpacaOptionSurface,
    spot_price: Decimal,
) -> tuple[StrikeSkewBucket, ...]:
    call_by_strike = _iv_by_strike(call)
    put_by_strike = _iv_by_strike(put)
    values = tuple(
        (
            strike * Decimal(10_000) / spot_price,
            put_by_strike[strike] - call_by_strike[strike],
        )
        for strike in sorted(call_by_strike.keys() & put_by_strike.keys())
    )
    buckets = tuple(
        StrikeSkewBucket(
            bucket_id=bucket_id,
            lower_moneyness_bps=lower,
            upper_moneyness_bps=upper,
            matched_strike_count=len(matches),
            median_put_minus_call_iv=_median(matches),
        )
        for bucket_id, lower, upper in STRIKE_BUCKET_RANGES
        if (matches := tuple(skew for moneyness, skew in values if Decimal(lower) <= moneyness < Decimal(upper)))
    )
    if not buckets:
        raise AlpacaOptionSkewError
    return buckets


def build_delta_skew_buckets(
    call: AlpacaOptionSurface,
    put: AlpacaOptionSurface,
) -> tuple[DeltaSkewBucket, ...]:
    call_values = _delta_values(call)
    put_values = _delta_values(put)
    buckets: list[DeltaSkewBucket] = []
    for bucket_id, lower, upper in DELTA_BUCKET_RANGES:
        call_iv = tuple(iv for delta, iv in call_values if lower <= delta < upper)
        put_iv = tuple(iv for delta, iv in put_values if lower <= delta < upper)
        if call_iv and put_iv:
            call_median = _median(call_iv)
            put_median = _median(put_iv)
            buckets.append(
                DeltaSkewBucket(
                    bucket_id=bucket_id,
                    lower_absolute_delta=lower,
                    upper_absolute_delta=upper,
                    call_observation_count=len(call_iv),
                    put_observation_count=len(put_iv),
                    call_median_iv=call_median,
                    put_median_iv=put_median,
                    put_minus_call_median_iv=put_median - call_median,
                )
            )
    if not buckets:
        raise AlpacaOptionSkewError
    return tuple(buckets)


def _iv_by_strike(
    surface: AlpacaOptionSurface,
) -> dict[Decimal, Decimal]:
    values = {
        contract.strike_price: contract.implied_volatility
        for contract in surface.contracts
        if contract.implied_volatility is not None
    }
    if len(values) != surface.implied_volatility_count:
        raise AlpacaOptionSkewError
    return values


def _delta_values(
    surface: AlpacaOptionSurface,
) -> tuple[tuple[Decimal, Decimal], ...]:
    values: list[tuple[Decimal, Decimal]] = []
    for contract in surface.contracts:
        if contract.implied_volatility is None or contract.greeks is None:
            continue
        delta = contract.greeks.delta
        match surface.contract_type:
            case OptionContractType.CALL:
                valid = Decimal(0) < delta <= Decimal(1)
            case OptionContractType.PUT:
                valid = Decimal(-1) <= delta < Decimal(0)
            case unreachable:
                assert_never(unreachable)
        if not valid:
            raise AlpacaOptionSkewError
        values.append((abs(delta), contract.implied_volatility))
    return tuple(values)


def _median(values: tuple[Decimal, ...]) -> Decimal:
    ordered = tuple(sorted(values))
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


__all__ = (
    "build_delta_skew_buckets",
    "build_strike_skew_buckets",
)
