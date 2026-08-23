from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json
from trading_agent.kr_day_capsule_models import KrDayCapsuleEvaluation

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class KrDayCapsuleShadowStatus(StrEnum):
    REGISTERED = "registered"
    ACTIVE = "active"
    STOPPED = "stopped"
    TARGETED = "targeted"
    CENSORED = "censored"
    BLOCKED = "blocked"
    FAILED = "failed"


class KrDayCapsuleShadowReason(StrEnum):
    NO_SIGNAL = "no_signal"
    ENTRY = "entry"
    ACTIVE = "active"
    STOP_FIRST = "stop_first"
    TARGET = "target"
    BAR_GAP = "bar_gap"
    DIVERGENT_BATCH = "divergent_batch"
    INVALID_EVALUATION = "invalid_evaluation"
    SIGNAL_BLOCKED = "signal_blocked"


class InvalidKrDayCapsuleShadowError(ValueError):
    @override
    def __str__(self) -> str:
        return "KR day capsule shadow event is invalid"


class KrDayCapsuleShadowModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")


class KrDayCapsuleShadowEventPayload(KrDayCapsuleShadowModel):
    schema_version: Literal[1] = 1
    capsule_id: str
    evaluation_id: str
    session_date: dt.date
    calendar_snapshot_id: str
    collection_cycle_id: str
    symbol: str
    attempted_bar_cursor: dt.datetime
    accepted_bar_cursor: dt.datetime | None
    previous_event_id: str | None
    status: KrDayCapsuleShadowStatus
    reason: KrDayCapsuleShadowReason
    signal_id: str | None
    entry_price: Decimal | None
    stop_price: Decimal | None
    target_prices: tuple[Decimal, ...]
    occurred_at: dt.datetime
    evaluation_payload_sha256: str
    bar_payload_sha256: str
    research_only: Literal[True] = True
    trading_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        hashes = (self.capsule_id, self.evaluation_id, self.evaluation_payload_sha256, self.bar_payload_sha256)
        if (
            not all(_HEX64.fullmatch(value) for value in hashes)
            or (self.previous_event_id is not None and _HEX64.fullmatch(self.previous_event_id) is None)
            or not self.calendar_snapshot_id
            or not self.collection_cycle_id
            or not self.symbol
            or not _aware(self.attempted_bar_cursor)
            or not _aware(self.occurred_at)
            or (self.accepted_bar_cursor is not None and not _aware(self.accepted_bar_cursor))
        ):
            raise InvalidKrDayCapsuleShadowError
        _require_status_shape(self)
        return self


class KrDayCapsuleShadowEvent(KrDayCapsuleShadowEventPayload):
    event_id: str

    @classmethod
    def canonical_id_for(cls, payload: KrDayCapsuleShadowEventPayload) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(payload).encode()).hexdigest()

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        payload = KrDayCapsuleShadowEventPayload.model_validate(
            self.model_dump(mode="python", exclude={"event_id"})
        )
        if self.event_id != self.canonical_id_for(payload):
            raise InvalidKrDayCapsuleShadowError
        return self

    @property
    def terminal(self) -> bool:
        return self.status in {
            KrDayCapsuleShadowStatus.STOPPED,
            KrDayCapsuleShadowStatus.TARGETED,
            KrDayCapsuleShadowStatus.CENSORED,
            KrDayCapsuleShadowStatus.BLOCKED,
            KrDayCapsuleShadowStatus.FAILED,
        }


def _require_status_shape(event: KrDayCapsuleShadowEventPayload) -> None:
    has_position = (
        event.signal_id is not None
        and event.entry_price is not None
        and event.stop_price is not None
        and bool(event.target_prices)
        and event.stop_price < event.entry_price < event.target_prices[0]
    )
    match event.status:
        case KrDayCapsuleShadowStatus.REGISTERED:
            valid = event.accepted_bar_cursor == event.attempted_bar_cursor and not has_position
        case KrDayCapsuleShadowStatus.ACTIVE | KrDayCapsuleShadowStatus.STOPPED | KrDayCapsuleShadowStatus.TARGETED:
            valid = event.accepted_bar_cursor == event.attempted_bar_cursor and has_position
        case KrDayCapsuleShadowStatus.CENSORED:
            valid = event.accepted_bar_cursor != event.attempted_bar_cursor
        case KrDayCapsuleShadowStatus.BLOCKED | KrDayCapsuleShadowStatus.FAILED:
            valid = event.accepted_bar_cursor != event.attempted_bar_cursor
        case unreachable:
            assert_never(unreachable)
    if not valid:
        raise InvalidKrDayCapsuleShadowError


def kr_day_capsule_evaluation_lineage_matches(evaluation: KrDayCapsuleEvaluation) -> bool:
    opportunity = evaluation.setup_input.opportunity
    cycle_ids = tuple(
        item.record_id
        for item in opportunity.evidence_refs
        if item.namespace == "kr/collection_cycle"
    )
    return (
        evaluation.setup_input.producer_strategy_version == evaluation.capsule_id
        and evaluation.setup_input.evaluated_at == evaluation.evaluated_at
        and opportunity.opportunity_id == evaluation.opportunity_id
        and opportunity.candidates[0].symbol == evaluation.symbol
        and cycle_ids == (evaluation.collection_cycle_id,)
        and all(bar.symbol == evaluation.symbol for bar in evaluation.setup_input.bars)
    )


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "InvalidKrDayCapsuleShadowError",
    "KrDayCapsuleShadowEvent",
    "KrDayCapsuleShadowEventPayload",
    "KrDayCapsuleShadowReason",
    "KrDayCapsuleShadowStatus",
    "kr_day_capsule_evaluation_lineage_matches",
)
