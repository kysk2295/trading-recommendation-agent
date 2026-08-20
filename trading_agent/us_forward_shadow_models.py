from __future__ import annotations

import datetime as dt
import hashlib
from enum import StrEnum
from itertools import pairwise
from typing import Literal, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.generated_strategy_protocol import BarFrame, CandidateFrame
from trading_agent.research_identity_models import MarketId
from trading_agent.signal_contract_models import EvidenceRef, QuoteValidation
from trading_agent.us_equity_calendar import NEW_YORK, regular_session_bounds


class InvalidUsForwardShadowTickError(ValueError):
    @override
    def __str__(self) -> str:
        return "us_forward_shadow_tick_invalid"


class UsForwardShadowStatus(StrEnum):
    REGISTERED = "registered"
    NO_SIGNAL = "no_signal"
    ENTERED = "entered"
    OBSERVED = "observed"
    EXITED = "exited"
    CENSORED = "censored"
    BLOCKED = "blocked"
    FAILED = "failed"
    REPLAYED = "replayed"


class UsForwardShadowCapsuleResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capsule_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    trial_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: UsForwardShadowStatus
    event_ids: tuple[str, ...]
    outcome_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reason_codes: tuple[str, ...] = ()
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False


class UsForwardShadowTickResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    policy_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_id: str = Field(pattern=r"^XNYS-[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    completed_bar_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    results: tuple[UsForwardShadowCapsuleResult, ...]
    trading_authority: Literal[False] = False
    profitability_claim: Literal[False] = False


class UsForwardShadowTick(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    schema_version: Literal[1] = 1
    market_id: MarketId
    policy_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    session_id: str = Field(pattern=r"^XNYS-[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    session_date: dt.date
    calendar_snapshot_id: str = Field(
        pattern=r"^calendar://official/XNYS/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    completed_bar_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_bar_sequence: int = Field(ge=1)
    bars: tuple[BarFrame, ...] = Field(min_length=1)
    candidate: CandidateFrame | None
    quote: QuoteValidation
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def validate_tick(self) -> Self:
        bounds = regular_session_bounds(self.session_date)
        latest = self.bars[-1]
        evidence_ids = tuple(item.canonical_id for item in self.evidence_refs)
        if (
            self.market_id is not MarketId.US_EQUITIES
            or bounds is None
            or self.session_id != f"XNYS-{self.session_date.isoformat()}"
            or self.completed_bar_id != completed_bar_id(latest)
            or not _bars_valid(self.bars, bounds)
            or not _current_time_valid(latest.timestamp, self.observed_at, bounds)
            or not _candidate_valid(self.candidate, latest)
            or not _quote_valid(self.quote, latest.timestamp, self.observed_at)
            or evidence_ids != tuple(sorted(set(evidence_ids)))
            or any(item.observed_at > self.observed_at for item in self.evidence_refs)
        ):
            raise InvalidUsForwardShadowTickError
        return self


def completed_bar_id(bar: BarFrame) -> str:
    checked = BarFrame.model_validate(bar.model_dump(mode="python"))
    return hashlib.sha256(canonical_experiment_ledger_json(checked).encode()).hexdigest()


def _bars_valid(
    bars: tuple[BarFrame, ...],
    bounds: tuple[dt.datetime, dt.datetime],
) -> bool:
    first_symbol = bars[0].symbol
    timestamps = tuple(item.timestamp for item in bars)
    if (
        any(item.symbol != first_symbol for item in bars)
        or timestamps != tuple(sorted(set(timestamps)))
        or any(not bounds[0] < timestamp < bounds[1] for timestamp in timestamps)
    ):
        return False
    if len(timestamps) < 3:
        return True
    intervals = tuple(right - left for left, right in pairwise(timestamps))
    return intervals[0] > dt.timedelta(0) and len(set(intervals)) == 1


def _current_time_valid(
    latest_at: dt.datetime,
    observed_at: dt.datetime,
    bounds: tuple[dt.datetime, dt.datetime],
) -> bool:
    return (
        latest_at <= observed_at < bounds[1]
        and bounds[0] < observed_at
        and observed_at - latest_at <= dt.timedelta(seconds=90)
    )


def _candidate_valid(candidate: CandidateFrame | None, latest: BarFrame) -> bool:
    return candidate is None or (
        candidate.symbol == latest.symbol and candidate.timestamp == latest.timestamp
    )


def _quote_valid(
    quote: QuoteValidation,
    latest_at: dt.datetime,
    observed_at: dt.datetime,
) -> bool:
    return (
        latest_at <= quote.observed_at <= observed_at <= quote.valid_until
        and observed_at - quote.observed_at <= dt.timedelta(seconds=5)
        and quote.spread_bps <= quote.max_slippage_bps
    )


def current_xnys_tick_at(tick: UsForwardShadowTick, evaluation_at: dt.datetime) -> bool:
    if evaluation_at.tzinfo is None or evaluation_at.utcoffset() is None:
        return False
    bounds = regular_session_bounds(tick.session_date)
    latest = tick.bars[-1]
    expected_completed_bar_at = evaluation_at.astimezone(dt.UTC).replace(second=0, microsecond=0)
    return (
        bounds is not None
        and tick.session_date == evaluation_at.astimezone(NEW_YORK).date()
        and tick.session_id == f"XNYS-{tick.session_date.isoformat()}"
        and bounds[0] < evaluation_at < bounds[1]
        and latest.timestamp == expected_completed_bar_at
        and tick.observed_at <= evaluation_at
        and _quote_valid(tick.quote, latest.timestamp, evaluation_at)
    )


__all__ = (
    "InvalidUsForwardShadowTickError",
    "UsForwardShadowCapsuleResult",
    "UsForwardShadowStatus",
    "UsForwardShadowTick",
    "UsForwardShadowTickResult",
    "completed_bar_id",
    "current_xnys_tick_at",
)
