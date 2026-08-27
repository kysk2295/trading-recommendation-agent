from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self, assert_never, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.kr_autonomous_trade_models import KrTradeRecommendation
from trading_agent.kr_theme_day_setup_progress import KrCompletedMinuteBar

_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
_SHA = r"^[a-f0-9]{64}$"
_KST: Final = ZoneInfo("Asia/Seoul")


class KrVirtualPositionState(StrEnum):
    ARMED = "ARMED"
    ACTIVE = "ACTIVE"
    STOPPED = "STOPPED"
    TARGETED = "TARGETED"
    EXPIRED = "EXPIRED"
    CENSORED = "CENSORED"


class KrVirtualPositionReason(StrEnum):
    ARMED = "armed"
    ENTRY = "entry"
    STOP_FIRST = "stop_first"
    TARGET = "target"
    ENTRY_EXPIRED = "entry_expired"
    SESSION_CLOSE = "session_close"
    BAR_GAP = "bar_gap"
    DIVERGENT_REPLAY = "divergent_replay"


class InvalidKrVirtualPositionError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR virtual position event is invalid"


@dataclass(frozen=True, slots=True)
class KrVirtualPositionTransition:
    state: KrVirtualPositionState
    reason: KrVirtualPositionReason
    attempted: dt.datetime | None
    accepted: dt.datetime | None
    fill_price: Decimal | None
    fill_time: dt.datetime | None
    exit_price: Decimal | None
    exit_time: dt.datetime | None
    evidence_refs: tuple[str, ...]


class KrVirtualPositionEvent(BaseModel):
    model_config = _STRICT

    schema_version: Literal[1] = 1
    event_id: str = Field(pattern=_SHA)
    position_id: str = Field(pattern=_SHA)
    recommendation_id: str = Field(pattern=_SHA)
    task_id: str = Field(pattern=_SHA)
    symbol: str
    theme: str
    sequence: int = Field(ge=1)
    previous_event_id: str | None = Field(default=None, pattern=_SHA)
    state: KrVirtualPositionState
    reason: KrVirtualPositionReason
    attempted_completed_bar_cursor: dt.datetime | None
    accepted_completed_bar_cursor: dt.datetime | None
    entry: Decimal
    stop: Decimal
    targets: tuple[Decimal, Decimal]
    quantity: int = Field(gt=0)
    fill_price: Decimal | None = None
    fill_time: dt.datetime | None = None
    exit_price: Decimal | None = None
    exit_time: dt.datetime | None = None
    occurred_at: dt.datetime
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=512)
    virtual_only: Literal[True] = True
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        cursors = tuple(
            value
            for value in (self.attempted_completed_bar_cursor, self.accepted_completed_bar_cursor)
            if value is not None
        )
        if (
            not _aware(self.occurred_at)
            or any(not _aware(value) for value in cursors)
            or any(value is not None and not _aware(value) for value in (self.fill_time, self.exit_time))
            or not self.stop < self.entry < self.targets[0] < self.targets[1]
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
            or self.event_id != virtual_position_event_id(self)
            or not _valid_state_shape(self)
        ):
            raise InvalidKrVirtualPositionError
        return self

    @property
    def terminal(self) -> bool:
        return self.state in {
            KrVirtualPositionState.STOPPED,
            KrVirtualPositionState.TARGETED,
            KrVirtualPositionState.EXPIRED,
            KrVirtualPositionState.CENSORED,
        }


def virtual_position_id(recommendation: KrTradeRecommendation) -> str:
    payload = json.dumps(
        {"recommendation_id": recommendation.event_id, "task_id": recommendation.task_id},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def virtual_position_event_id(event: KrVirtualPositionEvent) -> str:
    payload = json.dumps(
        event.model_dump(mode="json", exclude={"event_id"}),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def canonical_kr_virtual_position_event_json(event: KrVirtualPositionEvent) -> str:
    return json.dumps(event.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _valid_state_shape(event: KrVirtualPositionEvent) -> bool:
    filled = event.fill_price == event.entry and event.fill_time is not None
    exited = event.exit_time is not None
    reason = event.reason
    exit_price = event.exit_price
    match event.state:
        case KrVirtualPositionState.ARMED:
            return reason is KrVirtualPositionReason.ARMED and not filled and not exited
        case KrVirtualPositionState.ACTIVE:
            return reason is KrVirtualPositionReason.ENTRY and filled and not exited
        case KrVirtualPositionState.STOPPED:
            return reason is KrVirtualPositionReason.STOP_FIRST and filled and exited and exit_price == event.stop
        case KrVirtualPositionState.TARGETED:
            return reason is KrVirtualPositionReason.TARGET and filled and exited and exit_price == event.targets[0]
        case KrVirtualPositionState.EXPIRED:
            return (reason is KrVirtualPositionReason.ENTRY_EXPIRED and not filled and not exited) or (
                reason is KrVirtualPositionReason.SESSION_CLOSE and filled and exited and exit_price is not None
            )
        case KrVirtualPositionState.CENSORED:
            return reason in {KrVirtualPositionReason.BAR_GAP, KrVirtualPositionReason.DIVERGENT_REPLAY} and not exited
        case unreachable:
            assert_never(unreachable)


def validate_virtual_position_chains(events: tuple[KrVirtualPositionEvent, ...]) -> None:
    tails: dict[str, tuple[int, str]] = {}
    for event in events:
        tail = tails.get(event.position_id)
        expected_id = None if tail is None else tail[1]
        expected_sequence = 1 if tail is None else tail[0] + 1
        if event.previous_event_id != expected_id or event.sequence != expected_sequence:
            raise InvalidKrVirtualPositionError
        tails[event.position_id] = (event.sequence, event.event_id)


def validate_virtual_position_lineage(
    recommendation: KrTradeRecommendation,
    event: KrVirtualPositionEvent,
) -> None:
    if (
        event.position_id != virtual_position_id(recommendation)
        or event.recommendation_id != recommendation.event_id
        or event.task_id != recommendation.task_id
    ):
        raise InvalidKrVirtualPositionError


def partition_virtual_position_bars(
    event: KrVirtualPositionEvent,
    bars: tuple[KrCompletedMinuteBar, ...],
) -> tuple[tuple[KrCompletedMinuteBar, ...], tuple[KrCompletedMinuteBar, ...]]:
    cursor = event.accepted_completed_bar_cursor
    if cursor is None:
        return (), bars
    return tuple(bar for bar in bars if bar.end_at <= cursor), tuple(bar for bar in bars if bar.end_at > cursor)


def virtual_position_replay_is_exact(
    event: KrVirtualPositionEvent,
    bars: tuple[KrCompletedMinuteBar, ...],
) -> bool:
    return all(bar.evidence_ref.canonical_id in event.evidence_refs for bar in bars)


def virtual_position_bars_are_continuous(
    recommendation: KrTradeRecommendation,
    event: KrVirtualPositionEvent,
    bars: tuple[KrCompletedMinuteBar, ...],
) -> bool:
    local = recommendation.timestamp.astimezone(_KST)
    expected = event.accepted_completed_bar_cursor or local.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    return all(
        bar.symbol == recommendation.symbol
        and bar.start_at == expected + dt.timedelta(minutes=index)
        and bar.start_at.astimezone(_KST).date() == local.date()
        for index, bar in enumerate(bars)
    )


def copy_virtual_position_transition(
    event: KrVirtualPositionEvent,
    state: KrVirtualPositionState,
    reason: KrVirtualPositionReason,
) -> KrVirtualPositionTransition:
    return KrVirtualPositionTransition(
        state,
        reason,
        event.attempted_completed_bar_cursor,
        event.accepted_completed_bar_cursor,
        event.fill_price,
        event.fill_time,
        None,
        None,
        event.evidence_refs,
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
