from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

HERMES_DELIVERY_CONTRACT_VERSION: Final = "hermes-delivery-v1"
_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")


class InvalidHermesDeliveryModelError(ValueError):
    pass


class HermesDeliveryKind(StrEnum):
    WATCH = "watch"
    ACTIONABLE = "actionable"
    INVALIDATION = "invalidation"
    EXIT = "exit"
    INCIDENT = "incident"
    NO_RECOMMENDATION = "no_recommendation"
    RESEARCH = "research"
    DAILY_SUMMARY = "daily_summary"


class HermesDeliveryTransitionKind(StrEnum):
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTER = "dead_letter"


class HermesDeliveryFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    failed_at: dt.datetime
    reason: str
    retry_delay_seconds: int
    terminal: bool = False

    @model_validator(mode="after")
    def validate_failure(self) -> Self:
        if (
            not _aware(self.failed_at)
            or _IDENTIFIER.fullmatch(self.reason) is None
            or not 0 <= self.retry_delay_seconds <= 3600
        ):
            raise InvalidHermesDeliveryModelError("invalid Hermes delivery failure")
        return self


class HermesDeliveryEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    contract_version: Literal["hermes-delivery-v1"] = HERMES_DELIVERY_CONTRACT_VERSION
    delivery_id: str
    root_delivery_id: str
    kind: HermesDeliveryKind
    source_event_id: str
    market_id: str
    lane_id: str | None
    occurred_at: dt.datetime
    payload_sha256: str
    rendered_text: str
    agent_family: str | None = None
    instrument_id: str | None = None
    strategy_version: str | None = None
    status: str = "pending"
    evidence_refs: tuple[str, ...] = ()
    max_attempts: int = 3

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        identifiers = (self.source_event_id, self.market_id)
        if self.lane_id is not None:
            identifiers += (self.lane_id,)
        optional_identifiers = tuple(
            value for value in (self.agent_family, self.instrument_id, self.strategy_version) if value is not None
        )
        if (
            self.delivery_id != hermes_delivery_id(self.source_event_id, self.contract_version)
            or _HEX64.fullmatch(self.root_delivery_id) is None
            or _HEX64.fullmatch(self.payload_sha256) is None
            or not all(_IDENTIFIER.fullmatch(value) for value in identifiers)
            or not all(_IDENTIFIER.fullmatch(value) for value in optional_identifiers)
            or not _aware(self.occurred_at)
            or not self.rendered_text
            or self.rendered_text != self.rendered_text.strip()
            or len(self.rendered_text) > 4096
            or _IDENTIFIER.fullmatch(self.status) is None
            or self.evidence_refs != tuple(sorted(set(self.evidence_refs)))
            or any(not value or value != value.strip() or len(value) > 512 for value in self.evidence_refs)
            or not 1 <= self.max_attempts <= 10
        ):
            raise InvalidHermesDeliveryModelError("invalid Hermes delivery event")
        return self


class HermesDeliveryAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    attempt_id: str
    delivery_id: str
    attempt_number: int
    worker_id: str
    claimed_at: dt.datetime
    lease_expires_at: dt.datetime

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        if (
            self.attempt_id != hermes_attempt_id(self.delivery_id, self.attempt_number)
            or _HEX64.fullmatch(self.delivery_id) is None
            or not 1 <= self.attempt_number <= 10
            or _IDENTIFIER.fullmatch(self.worker_id) is None
            or not _aware(self.claimed_at)
            or not _aware(self.lease_expires_at)
            or self.lease_expires_at <= self.claimed_at
        ):
            raise InvalidHermesDeliveryModelError("invalid Hermes delivery attempt")
        return self


class HermesDeliveryTransition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    transition_id: str
    delivery_id: str
    attempt_id: str
    kind: HermesDeliveryTransitionKind
    occurred_at: dt.datetime
    available_at: dt.datetime | None
    reason: str

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        retry_geometry = self.kind is HermesDeliveryTransitionKind.RETRY_SCHEDULED and self.available_at is not None
        dead_geometry = self.kind is HermesDeliveryTransitionKind.DEAD_LETTER and self.available_at is None
        if (
            self.transition_id != hermes_transition_id(self.attempt_id, self.kind)
            or _HEX64.fullmatch(self.delivery_id) is None
            or _HEX64.fullmatch(self.attempt_id) is None
            or _IDENTIFIER.fullmatch(self.reason) is None
            or not _aware(self.occurred_at)
            or (self.available_at is not None and not _aware(self.available_at))
            or not (retry_geometry or dead_geometry)
            or (self.available_at is not None and self.available_at < self.occurred_at)
        ):
            raise InvalidHermesDeliveryModelError("invalid Hermes delivery transition")
        return self


class HermesDeliveryAcknowledgement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    acknowledgement_id: str
    delivery_id: str
    attempt_id: str
    platform_message_id: str
    acknowledged_at: dt.datetime

    @model_validator(mode="after")
    def validate_acknowledgement(self) -> Self:
        if (
            self.acknowledgement_id != hermes_acknowledgement_id(self.delivery_id, self.platform_message_id)
            or _HEX64.fullmatch(self.delivery_id) is None
            or _HEX64.fullmatch(self.attempt_id) is None
            or _IDENTIFIER.fullmatch(self.platform_message_id) is None
            or not _aware(self.acknowledged_at)
        ):
            raise InvalidHermesDeliveryModelError("invalid Hermes delivery acknowledgement")
        return self


class HermesReplyLineage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    delivery_id: str
    root_delivery_id: str
    root_platform_message_id: str | None


class HermesDeliveryClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)

    event: HermesDeliveryEvent
    attempt: HermesDeliveryAttempt
    lineage: HermesReplyLineage


class HermesDeliveryAppendResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    delivery_id: str
    inserted: bool


def build_hermes_delivery_event(
    *,
    kind: HermesDeliveryKind,
    source_event_id: str,
    market_id: str,
    lane_id: str | None,
    occurred_at: dt.datetime,
    payload_sha256: str,
    rendered_text: str,
    agent_family: str | None = None,
    instrument_id: str | None = None,
    strategy_version: str | None = None,
    status: str = "pending",
    evidence_refs: tuple[str, ...] = (),
    root_delivery_id: str | None = None,
    max_attempts: int = 3,
) -> HermesDeliveryEvent:
    delivery_id = hermes_delivery_id(source_event_id, HERMES_DELIVERY_CONTRACT_VERSION)
    return HermesDeliveryEvent(
        delivery_id=delivery_id,
        root_delivery_id=delivery_id if root_delivery_id is None else root_delivery_id,
        kind=kind,
        source_event_id=source_event_id,
        market_id=market_id,
        lane_id=lane_id,
        occurred_at=occurred_at,
        payload_sha256=payload_sha256,
        rendered_text=rendered_text,
        agent_family=agent_family,
        instrument_id=instrument_id,
        strategy_version=strategy_version,
        status=status,
        evidence_refs=evidence_refs,
        max_attempts=max_attempts,
    )


def hermes_delivery_id(source_event_id: str, contract_version: str) -> str:
    return _digest((contract_version, source_event_id))


def hermes_attempt_id(delivery_id: str, attempt_number: int) -> str:
    return _digest((delivery_id, str(attempt_number)))


def hermes_transition_id(attempt_id: str, kind: HermesDeliveryTransitionKind) -> str:
    return _digest((attempt_id, kind.value))


def hermes_acknowledgement_id(delivery_id: str, platform_message_id: str) -> str:
    return _digest((delivery_id, platform_message_id))


def _digest(parts: tuple[str, ...]) -> str:
    encoded = json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
