from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.kr_intraday_market_gate import KrMarketConstraintSnapshot
from trading_agent.kr_theme_day_setup_progress import KrCompletedMinuteBar

KST = ZoneInfo("Asia/Seoul")
_MARKET_VALIDITY = dt.timedelta(seconds=5)


class KrAutonomousMarketErrorReason(StrEnum):
    INVALID_INPUT = "invalid_input"
    CALENDAR_UNAVAILABLE = "calendar_unavailable"
    SESSION_UNAVAILABLE = "session_unavailable"
    MARKET_EVIDENCE_INVALID = "market_evidence_invalid"
    CREDENTIAL_BOUNDARY_FAILED = "credential_boundary_failed"


class KrAutonomousMarketError(ValueError):
    __slots__ = ("reason",)

    reason: KrAutonomousMarketErrorReason

    def __init__(self, reason: KrAutonomousMarketErrorReason) -> None:
        self.reason = reason
        super().__init__(reason.value)

    @override
    def __str__(self) -> str:
        return f"KR autonomous market corroboration failed: {self.reason.value}"


class KrAutonomousMarketCorroboration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    corroboration_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    social_signal_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    symbol: str
    session_date: dt.date
    calendar_snapshot_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    social_first_observed_at: dt.datetime
    market_response_at: dt.datetime
    observed_at: dt.datetime
    valid_until: dt.datetime
    latest_completed_bar: KrCompletedMinuteBar
    market_snapshot: KrMarketConstraintSnapshot
    spread_bps: Decimal
    trading_value_krw: Decimal
    receipt_count: int
    receipt_sha256s: tuple[str, ...]
    evidence_count: int
    evidence_ids: tuple[str, ...]

    @model_validator(mode="after")
    def validate_corroboration(self) -> Self:
        bid = self.market_snapshot.bid_price
        ask = self.market_snapshot.ask_price
        expected_evidence = tuple(
            sorted(
                (
                    *(item.canonical_id for item in self.market_snapshot.evidence_refs),
                    self.latest_completed_bar.evidence_ref.canonical_id,
                )
            )
        )
        expected_receipts = tuple(
            sorted(
                {
                    *(item.record_id for item in self.market_snapshot.evidence_refs),
                    self.latest_completed_bar.evidence_ref.record_id.partition(":")[0],
                }
            )
        )
        local_times = (
            self.social_first_observed_at,
            self.market_response_at,
            self.observed_at,
            self.latest_completed_bar.start_at,
            self.market_snapshot.observed_at,
        )
        valid_quote = bid is not None and ask is not None and bid <= ask
        if bid is None or ask is None:
            expected_spread = Decimal(-1)
        else:
            expected_spread = (ask - bid) / ((bid + ask) / Decimal(2)) * Decimal(10_000)
        latest_minute_end = self.observed_at.astimezone(KST).replace(second=0, microsecond=0)
        if (
            self.symbol != self.latest_completed_bar.symbol
            or self.symbol != self.market_snapshot.symbol
            or any(not _aware(value) for value in (*local_times, self.valid_until))
            or any(value.astimezone(KST).date() != self.session_date for value in local_times[1:])
            or not self.social_first_observed_at <= self.market_response_at <= self.observed_at < self.valid_until
            or self.market_response_at != max(self.latest_completed_bar.observed_at, self.market_snapshot.observed_at)
            or self.latest_completed_bar.end_at != latest_minute_end
            or self.latest_completed_bar.end_at > self.observed_at
            or self.valid_until != self.market_snapshot.observed_at + _MARKET_VALIDITY
            or not valid_quote
            or not _nonnegative_finite(self.spread_bps)
            or self.spread_bps != expected_spread
            or not _nonnegative_finite(self.trading_value_krw)
            or self.trading_value_krw != self.latest_completed_bar.trading_value_krw
            or self.receipt_sha256s != expected_receipts
            or self.receipt_count != len(self.receipt_sha256s)
            or self.receipt_count != 3
            or self.evidence_ids != expected_evidence
            or self.evidence_count != len(self.evidence_ids)
            or self.evidence_count != 3
            or not all(_sha256(value) for value in self.receipt_sha256s)
            or self.corroboration_id != corroboration_id(self)
        ):
            raise KrAutonomousMarketError(KrAutonomousMarketErrorReason.MARKET_EVIDENCE_INVALID)
        return self


def corroboration_id(result: KrAutonomousMarketCorroboration) -> str:
    payload = json.dumps(
        result.model_dump(mode="json", exclude={"corroboration_id"}),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def canonical_kr_autonomous_market_corroboration_json(
    result: KrAutonomousMarketCorroboration,
) -> str:
    trusted = KrAutonomousMarketCorroboration.model_validate(result.model_dump(mode="python"))
    return json.dumps(
        trusted.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _nonnegative_finite(value: Decimal) -> bool:
    return value.is_finite() and value >= 0


def _sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


__all__ = (
    "KrAutonomousMarketCorroboration",
    "KrAutonomousMarketError",
    "KrAutonomousMarketErrorReason",
    "canonical_kr_autonomous_market_corroboration_json",
    "corroboration_id",
)
