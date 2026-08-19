from __future__ import annotations

import datetime as dt
import hashlib
import json
from enum import StrEnum
from typing import assert_never

from pydantic import BaseModel, ConfigDict

type CanonicalInput = (
    None
    | bool
    | int
    | float
    | str
    | dt.date
    | dt.datetime
    | dt.timedelta
    | StrEnum
    | list[CanonicalInput]
    | tuple[CanonicalInput, ...]
    | dict[str, CanonicalInput]
)
type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]


class StrategyResearchContractError(ValueError):
    pass


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    @property
    def content_sha256(self) -> str:
        payload = json.dumps(
            _canonicalize(self.model_dump(mode="python")),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class ResearchAgentId(StrEnum):
    INTRADAY_MOMENTUM = "intraday_momentum"
    INTRADAY_MEAN_REVERSION = "intraday_mean_reversion"
    CATALYST_EVENT = "catalyst_event"
    SWING_TREND_REGIME = "swing_trend_regime"
    CROSS_SECTIONAL_QUANT = "cross_sectional_quant"
    DERIVATIVES_VOLATILITY = "derivatives_volatility"


class EvidenceKind(StrEnum):
    REAL = "real"
    FIXTURE = "fixture"
    SYNTHETIC = "synthetic"
    REPLAY = "replay"
    BACKTEST = "backtest"


class EvidenceUse(StrEnum):
    RESEARCH = "research"
    WIRING_ONLY = "wiring_only"


class LiveEligibilityPolicy(StrEnum):
    TASK3_CURRENT_SESSION_GATE_REQUIRED = "task3_current_session_gate_required"
    WIRING_ONLY_NO_LIVE_USE = "wiring_only_no_live_use"


class ExpectedDirection(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    TWO_SIDED = "two_sided"


class HypothesisStatus(StrEnum):
    DRAFTED = "drafted"
    PREREGISTERED = "preregistered"


class AttemptStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABORTED = "aborted"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CENSORED = "censored"


class TerminalOutcome(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


class SafeTerminalReason(StrEnum):
    CI_WIDTH_TOO_WIDE = "CI_WIDTH_TOO_WIDE"
    DATA_QUALITY_GATE_FAILED = "DATA_QUALITY_GATE_FAILED"
    EFFECT_DIRECTION_MISMATCH = "EFFECT_DIRECTION_MISMATCH"
    INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_OBSERVATIONS"
    PREREGISTERED_FALSIFICATION_MET = "PREREGISTERED_FALSIFICATION_MET"
    PREREGISTERED_SUPPORT_MET = "PREREGISTERED_SUPPORT_MET"


def aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _canonicalize(value: CanonicalInput) -> JsonValue:
    match value:
        case dt.datetime() as timestamp:
            normalized = timestamp.astimezone(dt.UTC) if aware(timestamp) else timestamp
            return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
        case dt.date() as date:
            return date.isoformat()
        case dt.timedelta() as duration:
            return duration.total_seconds()
        case StrEnum() as member:
            return member.value
        case None | bool() | int() | float() | str():
            return value
        case list() | tuple() as sequence:
            return [_canonicalize(item) for item in sequence]
        case dict() as mapping:
            return {key: _canonicalize(item) for key, item in mapping.items()}
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "AttemptStatus",
    "CanonicalModel",
    "EvidenceKind",
    "EvidenceUse",
    "ExpectedDirection",
    "HypothesisStatus",
    "LiveEligibilityPolicy",
    "ResearchAgentId",
    "SafeTerminalReason",
    "StrategyResearchContractError",
    "TerminalOutcome",
    "aware",
)
