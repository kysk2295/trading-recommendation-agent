from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.alpaca_option_chain_models import OptionFeed
from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

STRIKE_BUCKET_RANGES: Final = (
    ("moneyness_9000_9500", 9_000, 9_500),
    ("moneyness_9500_10000", 9_500, 10_000),
    ("moneyness_10000_10500", 10_000, 10_500),
    ("moneyness_10500_11000", 10_500, 11_000),
)
DELTA_BUCKET_RANGES: Final = (
    ("absolute_delta_1000_2500", Decimal("0.10"), Decimal("0.25")),
    ("absolute_delta_2500_4000", Decimal("0.25"), Decimal("0.40")),
    ("absolute_delta_4000_6000", Decimal("0.40"), Decimal("0.60")),
    ("absolute_delta_6000_7500", Decimal("0.60"), Decimal("0.75")),
    ("absolute_delta_7500_9000", Decimal("0.75"), Decimal("0.90")),
)


class OptionSkewStatus(StrEnum):
    READY = "ready"


class AlpacaOptionSkewError(ValueError):
    @override
    def __str__(self) -> str:
        return "source-backed Alpaca option skew is invalid"


class StrikeSkewBucket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bucket_id: str
    lower_moneyness_bps: int
    upper_moneyness_bps: int
    matched_strike_count: int = Field(ge=1, le=8_000)
    median_put_minus_call_iv: Decimal

    @model_validator(mode="after")
    def validate_bucket(self) -> Self:
        expected = {bucket_id: (lower, upper) for bucket_id, lower, upper in STRIKE_BUCKET_RANGES}
        if (
            expected.get(self.bucket_id) != (self.lower_moneyness_bps, self.upper_moneyness_bps)
            or not self.median_put_minus_call_iv.is_finite()
        ):
            raise AlpacaOptionSkewError
        return self


class DeltaSkewBucket(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bucket_id: str
    lower_absolute_delta: Decimal
    upper_absolute_delta: Decimal
    call_observation_count: int = Field(ge=1, le=8_000)
    put_observation_count: int = Field(ge=1, le=8_000)
    call_median_iv: Decimal = Field(ge=0)
    put_median_iv: Decimal = Field(ge=0)
    put_minus_call_median_iv: Decimal

    @model_validator(mode="after")
    def validate_bucket(self) -> Self:
        expected = {bucket_id: (lower, upper) for bucket_id, lower, upper in DELTA_BUCKET_RANGES}
        if (
            expected.get(self.bucket_id) != (self.lower_absolute_delta, self.upper_absolute_delta)
            or not all(
                value.is_finite()
                for value in (
                    self.call_median_iv,
                    self.put_median_iv,
                    self.put_minus_call_median_iv,
                )
            )
            or self.put_median_iv - self.call_median_iv != self.put_minus_call_median_iv
        ):
            raise AlpacaOptionSkewError
        return self


class AlpacaOptionSkew(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    status: OptionSkewStatus
    feed: OptionFeed
    underlying_symbol: str
    underlying_instrument_id: str
    expiration_date: dt.date
    call_surface_id: str
    call_surface_sha256: str
    call_surface_observed_at: dt.datetime
    put_surface_id: str
    put_surface_sha256: str
    put_surface_observed_at: dt.datetime
    spot_dataset_id: str
    spot_dataset_parquet_sha256: str
    spot_dataset_event_content_sha256: str
    spot_event_id: str
    spot_event_content_hash: str
    spot_raw_receipt_ref: str
    spot_runtime_receipt_id: str
    spot_runtime_payload_sha256: str
    spot_bar_started_at: dt.datetime
    spot_bar_completed_at: dt.datetime
    spot_price: Decimal = Field(gt=0)
    as_of: dt.datetime
    maximum_observation_skew_seconds: int = Field(ge=0, le=300)
    observation_skew_seconds: Decimal = Field(ge=0)
    strike_buckets: tuple[StrikeSkewBucket, ...] = Field(
        min_length=1,
        max_length=len(STRIKE_BUCKET_RANGES),
    )
    delta_buckets: tuple[DeltaSkewBucket, ...] = Field(
        min_length=1,
        max_length=len(DELTA_BUCKET_RANGES),
    )

    @model_validator(mode="after")
    def validate_skew(self) -> Self:
        hashes = (
            self.call_surface_id,
            self.call_surface_sha256,
            self.put_surface_id,
            self.put_surface_sha256,
            self.spot_dataset_id,
            self.spot_dataset_parquet_sha256,
            self.spot_dataset_event_content_sha256,
            self.spot_event_content_hash,
            self.spot_raw_receipt_ref,
            self.spot_runtime_receipt_id,
            self.spot_runtime_payload_sha256,
        )
        observed = (
            self.call_surface_observed_at,
            self.put_surface_observed_at,
            self.spot_bar_completed_at,
        )
        strike_ids = tuple(item.bucket_id for item in self.strike_buckets)
        delta_ids = tuple(item.bucket_id for item in self.delta_buckets)
        if (
            any(_SHA256.fullmatch(value) is None for value in hashes)
            or not self.underlying_symbol
            or not self.underlying_instrument_id
            or not self.spot_event_id
            or any(not _aware(value) for value in (*observed, self.as_of))
            or not _aware(self.spot_bar_started_at)
            or self.spot_bar_completed_at - self.spot_bar_started_at != dt.timedelta(minutes=1)
            or self.spot_bar_completed_at > min(self.call_surface_observed_at, self.put_surface_observed_at)
            or self.as_of != max(observed)
            or self.observation_skew_seconds != Decimal(str((max(observed) - min(observed)).total_seconds()))
            or self.observation_skew_seconds > self.maximum_observation_skew_seconds
            or strike_ids
            != tuple(bucket_id for bucket_id, _, _ in STRIKE_BUCKET_RANGES if bucket_id in set(strike_ids))
            or delta_ids != tuple(bucket_id for bucket_id, _, _ in DELTA_BUCKET_RANGES if bucket_id in set(delta_ids))
        ):
            raise AlpacaOptionSkewError
        return self

    @property
    def skew_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "DELTA_BUCKET_RANGES",
    "STRIKE_BUCKET_RANGES",
    "AlpacaOptionSkew",
    "AlpacaOptionSkewError",
    "DeltaSkewBucket",
    "OptionSkewStatus",
    "StrikeSkewBucket",
)
