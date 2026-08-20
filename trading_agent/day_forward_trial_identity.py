from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import assert_never
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from trading_agent.research_identity_models import MarketId


class ForwardExecutionLane(StrEnum):
    FORWARD_PROBE = "forward_probe"
    SHADOW = "shadow"


class DayForwardTrialEventKind(StrEnum):
    SIGNAL = "signal"
    ENTRY = "entry"
    OBSERVED = "observed"
    EXIT = "exit"
    NO_SIGNAL = "no_signal"
    BLOCKED = "blocked"
    FAILED = "failed"
    CENSORED = "censored"


class DayForwardExitReason(StrEnum):
    STOP = "stop"
    TARGET = "target"
    SESSION_END = "session_end"


type CanonicalScalar = (
    None
    | bool
    | int
    | float
    | str
    | dt.date
    | dt.datetime
    | MarketId
    | ForwardExecutionLane
    | DayForwardTrialEventKind
    | DayForwardExitReason
)
type CanonicalValue = (
    CanonicalScalar
    | BaseModel
    | dict[str, "CanonicalValue"]
    | tuple["CanonicalValue", ...]
    | list["CanonicalValue"]
)


def canonical_forward_trial_sha256(value: Mapping[str, CanonicalValue]) -> str:
    encoded = json.dumps(
        _canonical_value(dict(value)),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def market_clock(market_id: MarketId) -> tuple[str, ZoneInfo]:
    match market_id:
        case MarketId.US_EQUITIES:
            return "XNYS", ZoneInfo("America/New_York")
        case MarketId.KR_EQUITIES:
            return "XKRX", ZoneInfo("Asia/Seoul")
        case unreachable:
            assert_never(unreachable)


def _canonical_value(value: CanonicalValue) -> CanonicalValue:
    match value:
        case BaseModel() as model:
            return _canonical_value(model.model_dump(mode="python"))
        case dict() as mapping:
            return {
                key: _canonical_value(item)
                for key, item in mapping.items()
                if key != "schema_version"
            }
        case tuple() | list() as sequence:
            return [_canonical_value(item) for item in sequence]
        case dt.datetime() as timestamp:
            return timestamp.astimezone(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        case dt.date() as date:
            return date.isoformat()
        case MarketId() | ForwardExecutionLane() | DayForwardTrialEventKind() | DayForwardExitReason() as member:
            return member.value
        case None | bool() | int() | float() | str():
            return value
        case unreachable:
            assert_never(unreachable)


__all__ = (
    "CanonicalValue",
    "DayForwardExitReason",
    "DayForwardTrialEventKind",
    "ForwardExecutionLane",
    "canonical_forward_trial_sha256",
    "market_clock",
)
