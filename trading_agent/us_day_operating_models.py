from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, override

from trading_agent.hermes_arm_request import HermesArmConsumeCommand
from trading_agent.lane_identity_models import LaneId
from trading_agent.paper_execution_models import IntentId, PaperBrokerState
from trading_agent.paper_mutation_arm import PaperMutationArm
from trading_agent.paper_operating_session_models import PaperOrderAdmissionRequest
from trading_agent.us_equity_calendar import NEW_YORK

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class UsDayOperatingStatus(StrEnum):
    COMPLETED = "completed"
    BLOCKED = "blocked"
    INCIDENT = "incident"


class UsDayOperatingTransition(StrEnum):
    ACTIONABLE = "actionable"
    ENTRY_ACKNOWLEDGED = "entry_acknowledged"
    PROTECTIVE_OCO_ACKNOWLEDGED = "protective_oco_acknowledged"
    FLAT = "flat"
    RECONCILED = "reconciled"
    HERMES_RESULT_PROJECTED = "hermes_result_projected"


class InvalidUsDayOperatingRequestError(ValueError):
    @override
    def __str__(self) -> str:
        return "US Day operating request is invalid"


class InvalidUsDayOperatingConfigError(ValueError):
    @override
    def __str__(self) -> str:
        return "US Day operating coordinator config is invalid"


class UsDayArmConsumer(Protocol):
    def consume(self, command: HermesArmConsumeCommand, expected_strategy_version: str) -> PaperMutationArm: ...


@dataclass(frozen=True, slots=True)
class UsDayOperatingRequest:
    arm_request_id: str
    session_id: str
    strategy_version: str
    order_admission: PaperOrderAdmissionRequest
    quote_observed_at: dt.datetime
    evaluated_at: dt.datetime
    actionable_payload_sha256: str
    lane_id: LaneId = LaneId.INTRADAY_MOMENTUM

    def __post_init__(self) -> None:
        try:
            session_date = dt.date.fromisoformat(self.session_id[-10:])
        except ValueError:
            raise InvalidUsDayOperatingRequestError from None
        if (
            self.session_id != f"XNYS-{session_date.isoformat()}"
            or session_date != self.evaluated_at.astimezone(NEW_YORK).date()
            or self.lane_id is not LaneId.INTRADAY_MOMENTUM
            or not self.strategy_version
            or _SHA256.fullmatch(self.arm_request_id) is None
            or _SHA256.fullmatch(self.actionable_payload_sha256) is None
            or not _aware(self.quote_observed_at)
            or not _aware(self.evaluated_at)
        ):
            raise InvalidUsDayOperatingRequestError


@dataclass(frozen=True, slots=True)
class UsDayOperatingResult:
    status: UsDayOperatingStatus
    transitions: tuple[UsDayOperatingTransition, ...]
    reasons: tuple[str, ...]
    session_id: str
    strategy_version: str
    parent_intent_id: IntentId
    final_broker_state: PaperBrokerState | None
    actionable_delivery_id: str | None
    outcome_delivery_id: str


@dataclass(frozen=True, slots=True)
class ProjectedUsDayEvent:
    source_event_id: str
    delivery_id: str


@dataclass(frozen=True, slots=True)
class UsDayOperatingDraft:
    status: UsDayOperatingStatus
    transitions: tuple[UsDayOperatingTransition, ...]
    reasons: tuple[str, ...]
    final_broker_state: PaperBrokerState | None
    actionable: ProjectedUsDayEvent | None


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
