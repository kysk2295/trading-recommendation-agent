from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, Self, assert_never

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from trading_agent.research_identity_models import MarketId

type CanonicalScalar = (
    None
    | bool
    | int
    | float
    | str
    | dt.datetime
    | MarketId
    | DayDiscoveryDebitKind
    | DayDiscoveryEventKind
)
type CanonicalValue = (
    CanonicalScalar
    | BaseModel
    | dict[str, "CanonicalValue"]
    | tuple["CanonicalValue", ...]
    | list["CanonicalValue"]
)


class InvalidDayDiscoveryLedgerModelError(ValueError):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __str__(self) -> str:
        return self.reason


class DayDiscoveryEventKind(StrEnum):
    CYCLE_OPENED = "cycle_opened"
    CALL_RESERVED = "call_reserved"
    CALL_RESPONSE_RECORDED = "call_response_recorded"
    BRANCH_PREPARED = "branch_prepared"
    RESOLUTION_INTENT = "resolution_intent"
    ARTIFACT_VERIFIED = "artifact_verified"
    ARTIFACT_FAILED = "artifact_failed"
    ARTIFACT_OUTCOME_UNKNOWN = "artifact_outcome_unknown"
    PREFLIGHT_INTENT = "preflight_intent"
    PREFLIGHT_VERIFIED = "preflight_verified"
    PREFLIGHT_FAILED = "preflight_failed"
    PREFLIGHT_OUTCOME_UNKNOWN = "preflight_outcome_unknown"
    BRANCH_FINALIZED = "branch_finalized"
    CYCLE_FINALIZED = "cycle_finalized"


class DayDiscoveryDebitKind(StrEnum):
    CALL_RESERVATION = "call_reservation"
    CARTESIAN_TOP_UP = "cartesian_top_up"


class DayDiscoveryCallReservationPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    reservation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    account_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch_index: int = Field(ge=0, le=2)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_bytes_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_length: int = Field(ge=1, le=512 * 1024)
    model_id: str = Field(min_length=1, max_length=256)
    seed: int | None
    temperature: float = Field(ge=0, le=2)
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    creator: str = Field(min_length=1, max_length=256)
    creator_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reserved_at: AwareDatetime

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        if self.creator_sha256 != hashlib.sha256(self.creator.encode()).hexdigest():
            raise InvalidDayDiscoveryLedgerModelError("call_reservation_creator_invalid")
        if self.reservation_id != self.canonical_id_for(self.model_dump(mode="python")):
            raise InvalidDayDiscoveryLedgerModelError("call_reservation_identity_invalid")
        return self

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, CanonicalValue]) -> str:
        canonical = dict(payload)
        canonical.pop("reservation_id", None)
        return _sha(_canonical(canonical))


class DayDiscoveryCallResponsePayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    reservation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_base64: str = Field(min_length=4, max_length=350 * 1024)
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    response_length: int = Field(ge=1, le=256 * 1024)
    invocation_started_at: AwareDatetime
    received_at: AwareDatetime

    @model_validator(mode="after")
    def raw_response_matches_commitment(self) -> Self:
        try:
            raw = base64.b64decode(self.response_base64, validate=True)
        except ValueError:
            raise InvalidDayDiscoveryLedgerModelError("call_response_encoding_invalid") from None
        if (
            len(raw) != self.response_length
            or hashlib.sha256(raw).hexdigest() != self.response_sha256
        ):
            raise InvalidDayDiscoveryLedgerModelError("call_response_commitment_invalid")
        if self.received_at < self.invocation_started_at:
            raise InvalidDayDiscoveryLedgerModelError("call_response_time_invalid")
        return self


class DayDiscoveryBudgetAccount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    account_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    market_id: MarketId
    budget_epoch_ref: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    debit_limit: int = Field(ge=1, le=10_000)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        if self.account_id != self.canonical_id_for(self.model_dump(mode="python")):
            raise InvalidDayDiscoveryLedgerModelError("budget_account_identity_invalid")
        return self

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, CanonicalValue]) -> str:
        return _sha(
            _canonical(
                {
                    "market_id": payload["market_id"],
                    "budget_epoch_ref": payload["budget_epoch_ref"],
                    "protocol": "day-discovery-budget-v1",
                }
            )
        )


class DayDiscoveryCycle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    account_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    market_id: MarketId
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cursor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    opened_at: AwareDatetime

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        if self.cycle_id != self.canonical_id_for(self.model_dump(mode="python")):
            raise InvalidDayDiscoveryLedgerModelError("cycle_identity_invalid")
        return self

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, CanonicalValue]) -> str:
        canonical = dict(payload)
        canonical.pop("cycle_id", None)
        return _sha(_canonical(canonical))


class DayDiscoveryBudgetDebit(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    debit_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    account_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    branch_index: int = Field(ge=0, le=2)
    debit_kind: DayDiscoveryDebitKind
    amount: int = Field(ge=1, le=10_000)
    debited_at: AwareDatetime

    @model_validator(mode="after")
    def canonical_identity(self) -> Self:
        if self.debit_id != self.canonical_id_for(self.model_dump(mode="python")):
            raise InvalidDayDiscoveryLedgerModelError("debit_identity_invalid")
        return self

    @classmethod
    def canonical_id_for(cls, payload: Mapping[str, CanonicalValue]) -> str:
        canonical = dict(payload)
        canonical.pop("debit_id", None)
        return _sha(_canonical(canonical))


class DayDiscoveryEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    cycle_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    sequence: int = Field(ge=1)
    previous_event_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    branch_index: int | None = Field(default=None, ge=0, le=2)
    event_kind: DayDiscoveryEventKind
    event_at: AwareDatetime
    payload_json: str = Field(min_length=2, max_length=512 * 1024)

    @model_validator(mode="after")
    def canonical_event(self) -> Self:
        parsed_payload = json.loads(self.payload_json)
        canonical_payload = json.dumps(
            parsed_payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if self.payload_json != canonical_payload:
            raise InvalidDayDiscoveryLedgerModelError("event_payload_invalid")
        if self.sequence == 1 and self.previous_event_id is not None:
            raise InvalidDayDiscoveryLedgerModelError("event_predecessor_invalid")
        if self.sequence > 1 and self.previous_event_id is None:
            raise InvalidDayDiscoveryLedgerModelError("event_predecessor_invalid")
        if self.event_id != self.canonical_id_for(self.model_dump(mode="python")):
            raise InvalidDayDiscoveryLedgerModelError("event_identity_invalid")
        return self

    @classmethod
    def canonical_id_for(
        cls,
        payload: Mapping[str, CanonicalValue],
    ) -> str:
        canonical = dict(payload)
        canonical.pop("event_id", None)
        return _sha(_canonical(canonical))


def _canonical(value: CanonicalValue) -> str:
    match value:
        case BaseModel() as model:
            return _canonical(model.model_dump(mode="json"))
        case dt.datetime() as timestamp:
            normalized = timestamp.astimezone(dt.UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
            return json.dumps(normalized, separators=(",", ":"))
        case MarketId() | DayDiscoveryDebitKind() | DayDiscoveryEventKind() as member:
            return json.dumps(member.value, separators=(",", ":"))
        case dict() as mapping:
            normalized = {
                str(key): json.loads(_canonical(item))
                for key, item in mapping.items()
                if key != "schema_version"
            }
            return json.dumps(normalized, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        case tuple() | list() as sequence:
            return json.dumps(
                [json.loads(_canonical(item)) for item in sequence],
                ensure_ascii=True,
                separators=(",", ":"),
            )
        case None | bool() | int() | float() | str():
            return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        case unreachable:
            assert_never(unreachable)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


__all__ = (
    "DayDiscoveryBudgetAccount",
    "DayDiscoveryBudgetDebit",
    "DayDiscoveryCallReservationPayload",
    "DayDiscoveryCallResponsePayload",
    "DayDiscoveryCycle",
    "DayDiscoveryDebitKind",
    "DayDiscoveryEvent",
    "DayDiscoveryEventKind",
    "InvalidDayDiscoveryLedgerModelError",
)
