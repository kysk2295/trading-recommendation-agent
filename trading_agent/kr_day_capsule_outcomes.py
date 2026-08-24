from __future__ import annotations

import datetime as dt
import hashlib
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise
from typing import Literal, Self, TypedDict, assert_never, override
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_shadow_models import (
    KrDayCapsuleShadowEvent,
    KrDayCapsuleShadowReason,
    KrDayCapsuleShadowStatus,
)
from trading_agent.kr_theme_day_shadow_exit_models import SHADOW_EXIT_SLIPPAGE_BPS

_HEX64 = r"^[0-9a-f]{64}$"
_KST = ZoneInfo("Asia/Seoul")
_SESSION_CLOSE = dt.time(15, 30)


class KrDayCapsuleTerminalKind(StrEnum):
    EXIT = "exit"
    NO_SIGNAL = "no_signal"
    BLOCKED = "blocked"
    FAILED = "failed"
    CENSORED = "censored"


class InvalidKrDayCapsuleOutcomeError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day capsule terminal outcome is invalid"


class KrDayCapsuleOutcomeModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class KrDayCapsuleOutcomeAttempt(KrDayCapsuleOutcomeModel):
    attempt_id: str = Field(min_length=1)
    capsule_id: str = Field(pattern=_HEX64)
    hypothesis_version_id: str = Field(pattern=_HEX64)
    trial_id: str = Field(min_length=1)
    session_date: dt.date
    events: tuple[KrDayCapsuleShadowEvent, ...] = Field(min_length=1)
    session_close_price: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_lineage(self) -> Self:
        if (
            self.attempt_id != self.attempt_id.strip()
            or self.trial_id != self.trial_id.strip()
            or any(
                event.capsule_id != self.capsule_id or event.session_date != self.session_date
                or event.attempted_bar_cursor.astimezone(_KST).date() != self.session_date
                for event in self.events
            )
        ):
            raise InvalidKrDayCapsuleOutcomeError
        return self


class KrDayCapsuleOutcomeFields(TypedDict):
    attempt_id: str
    capsule_id: str
    hypothesis_version_id: str
    trial_id: str
    session_date: dt.date
    kind: KrDayCapsuleTerminalKind
    reason: str
    terminal_event_id: str
    net_return: Decimal | int | None
    realized_r: Decimal | int | None


class KrDayCapsuleOutcomePayload(KrDayCapsuleOutcomeModel):
    schema_version: Literal[1] = 1
    attempt_id: str = Field(min_length=1)
    capsule_id: str = Field(pattern=_HEX64)
    hypothesis_version_id: str = Field(pattern=_HEX64)
    trial_id: str = Field(min_length=1)
    session_date: dt.date
    kind: KrDayCapsuleTerminalKind
    reason: str = Field(min_length=1)
    terminal_event_id: str = Field(pattern=_HEX64)
    net_return: Decimal | None
    realized_r: Decimal | None
    market_id: Literal["kr_equities"] = "kr_equities"
    authority_ceiling: Literal["shadow_candidate"] = "shadow_candidate"
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_terminal_shape(self) -> Self:
        has_metrics = self.net_return is not None and self.realized_r is not None
        match self.kind:
            case KrDayCapsuleTerminalKind.EXIT:
                valid = has_metrics and self.net_return is not None and self.net_return > -1
            case (
                KrDayCapsuleTerminalKind.NO_SIGNAL
                | KrDayCapsuleTerminalKind.BLOCKED
                | KrDayCapsuleTerminalKind.FAILED
                | KrDayCapsuleTerminalKind.CENSORED
            ):
                valid = not has_metrics and self.net_return is None and self.realized_r is None
            case unreachable:
                assert_never(unreachable)
        if not valid:
            raise InvalidKrDayCapsuleOutcomeError
        return self


class KrDayCapsuleOutcome(KrDayCapsuleOutcomePayload):
    outcome_id: str = Field(pattern=_HEX64)

    @classmethod
    def seal(cls, fields: KrDayCapsuleOutcomeFields) -> KrDayCapsuleOutcome:
        payload = KrDayCapsuleOutcomePayload.model_validate(fields)
        return cls(
            outcome_id=hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest(),
            **payload.model_dump(mode="python"),
        )

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        payload = KrDayCapsuleOutcomePayload.model_validate(
            self.model_dump(mode="python", exclude={"outcome_id"})
        )
        expected = hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()
        if self.outcome_id != expected:
            raise InvalidKrDayCapsuleOutcomeError
        return self


def project_kr_day_capsule_outcome(
    attempt: KrDayCapsuleOutcomeAttempt,
) -> KrDayCapsuleOutcome:
    checked = KrDayCapsuleOutcomeAttempt.model_validate(attempt.model_dump(mode="python"))
    _require_contiguous_chain(checked.events)
    terminal = checked.events[-1]
    kind, reason, net_return, realized_r = _project_terminal(terminal, checked.session_close_price)
    return KrDayCapsuleOutcome.seal(
        {
            "attempt_id": checked.attempt_id,
            "capsule_id": checked.capsule_id,
            "hypothesis_version_id": checked.hypothesis_version_id,
            "trial_id": checked.trial_id,
            "session_date": checked.session_date,
            "kind": kind,
            "reason": reason,
            "terminal_event_id": terminal.event_id,
            "net_return": net_return,
            "realized_r": realized_r,
        }
    )


def _require_contiguous_chain(events: tuple[KrDayCapsuleShadowEvent, ...]) -> None:
    for index, event in enumerate(events):
        expected_previous = None if index == 0 else events[index - 1].event_id
        if event.previous_event_id != expected_previous:
            raise InvalidKrDayCapsuleOutcomeError
    for left, right in pairwise(events):
        delta = right.attempted_bar_cursor - left.attempted_bar_cursor
        gap_censor = (
            right is events[-1]
            and right.status is KrDayCapsuleShadowStatus.CENSORED
            and right.reason is KrDayCapsuleShadowReason.BAR_GAP
            and delta > dt.timedelta(minutes=1)
            and right.accepted_bar_cursor == left.accepted_bar_cursor
        )
        if delta != dt.timedelta(minutes=1) and not gap_censor:
            raise InvalidKrDayCapsuleOutcomeError


def _project_terminal(
    event: KrDayCapsuleShadowEvent,
    session_close_price: Decimal | None,
) -> tuple[KrDayCapsuleTerminalKind, str, Decimal | None, Decimal | None]:
    match event.status:
        case KrDayCapsuleShadowStatus.STOPPED:
            if event.reason is not KrDayCapsuleShadowReason.STOP_FIRST:
                raise InvalidKrDayCapsuleOutcomeError
            return _exit_metrics(event, event.stop_price, "stopped")
        case KrDayCapsuleShadowStatus.TARGETED:
            if event.reason is not KrDayCapsuleShadowReason.TARGET:
                raise InvalidKrDayCapsuleOutcomeError
            trigger = event.target_prices[0] if event.target_prices else None
            return _exit_metrics(event, trigger, "targeted")
        case KrDayCapsuleShadowStatus.REGISTERED:
            return KrDayCapsuleTerminalKind.NO_SIGNAL, event.reason.value, None, None
        case KrDayCapsuleShadowStatus.BLOCKED:
            return KrDayCapsuleTerminalKind.BLOCKED, event.reason.value, None, None
        case KrDayCapsuleShadowStatus.FAILED:
            return KrDayCapsuleTerminalKind.FAILED, event.reason.value, None, None
        case KrDayCapsuleShadowStatus.CENSORED:
            return KrDayCapsuleTerminalKind.CENSORED, event.reason.value, None, None
        case KrDayCapsuleShadowStatus.ACTIVE:
            if (
                event.attempted_bar_cursor.astimezone(_KST).time() == _SESSION_CLOSE
                and session_close_price is not None
            ):
                return _exit_metrics(event, session_close_price, "time_exit")
            raise InvalidKrDayCapsuleOutcomeError
        case unreachable:
            assert_never(unreachable)


def _exit_metrics(
    event: KrDayCapsuleShadowEvent,
    trigger: Decimal | None,
    reason: str,
) -> tuple[KrDayCapsuleTerminalKind, str, Decimal, Decimal]:
    entry = event.entry_price
    stop = event.stop_price
    if entry is None or stop is None or trigger is None:
        raise InvalidKrDayCapsuleOutcomeError
    exit_price = trigger * (Decimal(1) - SHADOW_EXIT_SLIPPAGE_BPS / Decimal(10_000))
    return (
        KrDayCapsuleTerminalKind.EXIT,
        reason,
        exit_price / entry - Decimal(1),
        (exit_price - entry) / (entry - stop),
    )


project_capsule_outcome = project_kr_day_capsule_outcome

__all__ = (
    "InvalidKrDayCapsuleOutcomeError",
    "KrDayCapsuleOutcome",
    "KrDayCapsuleOutcomeAttempt",
    "KrDayCapsuleOutcomeFields",
    "KrDayCapsuleTerminalKind",
    "project_capsule_outcome",
    "project_kr_day_capsule_outcome",
)
