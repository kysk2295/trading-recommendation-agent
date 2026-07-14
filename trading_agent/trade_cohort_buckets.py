from __future__ import annotations

from trading_agent.trade_cohort_models import (
    DollarVolumeBucket,
    GapBucket,
    PriceBucket,
    VolumeToAdvBucket,
)


def price_bucket(value: float) -> PriceBucket:
    if value < 5.0:
        return PriceBucket.LT_5
    if value < 20.0:
        return PriceBucket.FROM_5_TO_20
    if value < 50.0:
        return PriceBucket.FROM_20_TO_50
    return PriceBucket.GE_50


def gap_bucket(value: float) -> GapBucket:
    if value < 0.04:
        return GapBucket.LT_4_PCT
    if value < 0.10:
        return GapBucket.FROM_4_TO_10_PCT
    if value < 0.20:
        return GapBucket.FROM_10_TO_20_PCT
    return GapBucket.GE_20_PCT


def volume_to_adv_bucket(value: float) -> VolumeToAdvBucket:
    if value < 0.10:
        return VolumeToAdvBucket.LT_10_PCT
    if value < 0.25:
        return VolumeToAdvBucket.FROM_10_TO_25_PCT
    if value < 0.50:
        return VolumeToAdvBucket.FROM_25_TO_50_PCT
    return VolumeToAdvBucket.GE_50_PCT


def dollar_volume_bucket(value: float) -> DollarVolumeBucket:
    if value < 1_000_000.0:
        return DollarVolumeBucket.LT_1M
    if value < 5_000_000.0:
        return DollarVolumeBucket.FROM_1_TO_5M
    if value < 20_000_000.0:
        return DollarVolumeBucket.FROM_5_TO_20M
    return DollarVolumeBucket.GE_20M
