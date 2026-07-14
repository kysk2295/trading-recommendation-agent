from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class FeatureStatus(StrEnum):
    COMPLETE = "complete"
    CENSORED = "censored"


class PriceBucket(StrEnum):
    LT_5 = "price_lt_5"
    FROM_5_TO_20 = "price_5_20"
    FROM_20_TO_50 = "price_20_50"
    GE_50 = "price_ge_50"


class GapBucket(StrEnum):
    LT_4_PCT = "gap_lt_4pct"
    FROM_4_TO_10_PCT = "gap_4_10pct"
    FROM_10_TO_20_PCT = "gap_10_20pct"
    GE_20_PCT = "gap_ge_20pct"


class VolumeToAdvBucket(StrEnum):
    LT_10_PCT = "volume_to_adv_lt_10pct"
    FROM_10_TO_25_PCT = "volume_to_adv_10_25pct"
    FROM_25_TO_50_PCT = "volume_to_adv_25_50pct"
    GE_50_PCT = "volume_to_adv_ge_50pct"


class DollarVolumeBucket(StrEnum):
    LT_1M = "dollar_volume_lt_1m"
    FROM_1_TO_5M = "dollar_volume_1_5m"
    FROM_5_TO_20M = "dollar_volume_5_20m"
    GE_20M = "dollar_volume_ge_20m"


@dataclass(frozen=True, slots=True)
class TradeFeatureSource:
    database: Path
    risk_path: Path
    gap_path: Path | None


@dataclass(frozen=True, slots=True)
class TradeFeatureAssignment:
    recommendation_id: str
    symbol: str
    decision_at: dt.datetime
    status: FeatureStatus
    reason: str
    exchange: str | None = None
    candidate_observed_at: dt.datetime | None = None
    risk_observed_at: dt.datetime | None = None
    gap_observed_at: dt.datetime | None = None
    price: float | None = None
    change_pct: float | None = None
    opening_gap_pct: float | None = None
    volume_to_adv: float | None = None
    dollar_volume: float | None = None
    spread_bps: float | None = None
    price_bucket: PriceBucket | None = None
    gap_bucket: GapBucket | None = None
    volume_to_adv_bucket: VolumeToAdvBucket | None = None
    dollar_volume_bucket: DollarVolumeBucket | None = None
