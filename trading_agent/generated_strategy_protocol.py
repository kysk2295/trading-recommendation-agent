from __future__ import annotations

import math
from typing import Annotated, Final, Literal, Self, assert_never, override

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.models import BarInput, MomentumCandidate, StrategySignal

MAX_FRAME_BYTES: Final = 64 * 1024


class GeneratedStrategyProtocolError(ValueError):
    __slots__ = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f"generated strategy protocol invalid: {self.reason}"


class BarFrame(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    symbol: str = Field(min_length=1, max_length=32)
    timestamp: AwareDatetime
    open: float
    high: float
    low: float
    close: float
    volume: int = Field(ge=0)
    prior_close: float = Field(gt=0.0)
    average_daily_volume: int = Field(gt=0)
    spread_bps: float = Field(ge=0.0)
    catalyst: str = Field(max_length=4_096)

    @model_validator(mode="after")
    def validate_prices(self) -> Self:
        prices = (self.open, self.high, self.low, self.close, self.spread_bps)
        if (
            not all(math.isfinite(value) for value in prices)
            or self.low <= 0.0
            or self.high < self.low
            or not self.low <= self.open <= self.high
            or not self.low <= self.close <= self.high
        ):
            raise GeneratedStrategyProtocolError("bar_invalid")
        return self


class CandidateFrame(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    symbol: str = Field(min_length=1, max_length=32)
    timestamp: AwareDatetime
    price: float = Field(gt=0.0)
    gap_pct: float
    change_pct: float
    relative_volume: float = Field(ge=0.0)
    cumulative_dollar_volume: float = Field(ge=0.0)
    spread_bps: float = Field(ge=0.0)
    catalyst: str = Field(max_length=4_096)

    @model_validator(mode="after")
    def validate_numbers(self) -> Self:
        values = (
            self.price,
            self.gap_pct,
            self.change_pct,
            self.relative_volume,
            self.cumulative_dollar_volume,
            self.spread_bps,
        )
        if not all(math.isfinite(value) for value in values):
            raise GeneratedStrategyProtocolError("candidate_invalid")
        return self


class RunnerReady(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["ready"] = "ready"
    sequence: Literal[0] = 0


class ObserveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["observe"] = "observe"
    sequence: int = Field(ge=1)
    bar: BarFrame
    candidate: CandidateFrame | None


class NoSignalResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["no_signal"] = "no_signal"
    sequence: int = Field(ge=1)


class SignalResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    kind: Literal["signal"] = "signal"
    sequence: int = Field(ge=1)
    symbol: str = Field(min_length=1, max_length=32)
    timestamp: AwareDatetime
    entry: float = Field(gt=0.0)
    stop: float = Field(gt=0.0)
    rationale: str = Field(min_length=1, max_length=4_096)

    @model_validator(mode="after")
    def validate_signal(self) -> Self:
        if not math.isfinite(self.entry) or not math.isfinite(self.stop) or self.entry <= self.stop:
            raise GeneratedStrategyProtocolError("signal_prices_invalid")
        return self


class RunnerFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["failure"] = "failure"
    sequence: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=128)


type RunnerFrame = Annotated[
    RunnerReady | NoSignalResponse | SignalResponse | RunnerFailure,
    Field(discriminator="kind"),
]
_RUNNER_ADAPTER: Final = TypeAdapter(RunnerFrame)
_REQUEST_ADAPTER: Final = TypeAdapter(ObserveRequest)


def observe_request(
    sequence: int,
    bar: BarInput,
    candidate: MomentumCandidate | None,
) -> ObserveRequest:
    return ObserveRequest(
        sequence=sequence,
        bar=BarFrame.model_validate(_bar_fields(bar)),
        candidate=None if candidate is None else CandidateFrame.model_validate(_candidate_fields(candidate)),
    )


def encode_frame(frame: BaseModel) -> bytes:
    payload = (canonical_experiment_ledger_json(frame) + "\n").encode()
    if len(payload) > MAX_FRAME_BYTES:
        raise GeneratedStrategyProtocolError("frame_too_large")
    return payload


def parse_runner_frame(payload: bytes) -> RunnerFrame:
    _require_frame(payload)
    try:
        return _RUNNER_ADAPTER.validate_json(payload)
    except (GeneratedStrategyProtocolError, TypeError, ValidationError, ValueError):
        raise GeneratedStrategyProtocolError("runner_frame_invalid") from None


def parse_observe_frame(payload: bytes) -> ObserveRequest:
    _require_frame(payload)
    try:
        return _REQUEST_ADAPTER.validate_json(payload)
    except (GeneratedStrategyProtocolError, TypeError, ValidationError, ValueError):
        raise GeneratedStrategyProtocolError("observe_frame_invalid") from None


def signal_from_response(
    request: ObserveRequest,
    response: NoSignalResponse | SignalResponse | RunnerFailure,
    strategy_name: str,
) -> StrategySignal | None:
    if response.sequence != request.sequence:
        raise GeneratedStrategyProtocolError("sequence_mismatch")
    match response:
        case NoSignalResponse():
            return None
        case SignalResponse():
            if response.symbol != request.bar.symbol or response.timestamp != request.bar.timestamp:
                raise GeneratedStrategyProtocolError("bar_identity_mismatch")
            return StrategySignal(
                symbol=response.symbol,
                timestamp=response.timestamp,
                strategy=strategy_name,
                entry=response.entry,
                stop=response.stop,
                rationale=response.rationale,
            )
        case RunnerFailure(reason=reason):
            raise GeneratedStrategyProtocolError(reason)
        case unexpected:
            assert_never(unexpected)


def _require_frame(payload: bytes) -> None:
    if not payload or len(payload) > MAX_FRAME_BYTES or payload.count(b"\n") != 1 or not payload.endswith(b"\n"):
        raise GeneratedStrategyProtocolError("frame_shape_invalid")


def _bar_fields(bar: BarInput) -> dict[str, str | float | int]:
    return {
        "symbol": bar.symbol,
        "timestamp": bar.timestamp.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "prior_close": bar.prior_close,
        "average_daily_volume": bar.average_daily_volume,
        "spread_bps": bar.spread_bps,
        "catalyst": bar.catalyst,
    }


def _candidate_fields(candidate: MomentumCandidate) -> dict[str, str | float]:
    return {
        "symbol": candidate.symbol,
        "timestamp": candidate.timestamp.isoformat(),
        "price": candidate.price,
        "gap_pct": candidate.gap_pct,
        "change_pct": candidate.change_pct,
        "relative_volume": candidate.relative_volume,
        "cumulative_dollar_volume": candidate.cumulative_dollar_volume,
        "spread_bps": candidate.spread_bps,
        "catalyst": candidate.catalyst,
    }
