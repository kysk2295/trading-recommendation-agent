from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from typing import Final, Literal, Self, override

from pydantic import BaseModel, ConfigDict, StrictStr, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

KIS_CALENDAR_SOURCE_COMMIT: Final = "885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc"
KIS_CALENDAR_ADAPTER_VERSION: Final = "kis-chk-holiday-v1"
_HEX64: Final = re.compile(r"^[0-9a-f]{64}$")


class KisKrSessionCalendarEvidenceError(ValueError):
    @override
    def __str__(self) -> str:
        return "KIS KR session calendar evidence is invalid"


@dataclass(frozen=True, slots=True)
class KisKrSessionCalendarReceipt:
    base_date: dt.date
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            not _aware(self.received_at)
            or self.received_at.astimezone(dt.timezone(dt.timedelta(hours=9))).date() != self.base_date
            or type(self.status_code) is not int
            or not 100 <= self.status_code <= 599
            or self.content_type != "application/json"
            or type(self.raw_payload) is not bytes
            or not self.raw_payload
        ):
            raise KisKrSessionCalendarEvidenceError

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(self.raw_payload).hexdigest()


class KisKrSessionCalendarRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    bass_dt: StrictStr
    wday_dvsn_cd: StrictStr
    bzdy_yn: StrictStr
    tr_day_yn: StrictStr
    opnd_yn: StrictStr
    sttl_day_yn: StrictStr


class KisKrSessionCalendarEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rt_cd: StrictStr
    msg_cd: StrictStr
    msg1: StrictStr
    ctx_area_fk: StrictStr
    ctx_area_nk: StrictStr
    output: tuple[KisKrSessionCalendarRow, ...]


class KrSessionDay(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    session_date: dt.date
    weekday_code: str
    business_day: bool
    trading_day: bool
    open_day: bool
    settlement_day: bool

    @model_validator(mode="after")
    def validate_day(self) -> Self:
        if (
            not self.weekday_code
            or self.weekday_code != self.weekday_code.strip()
            or (self.open_day and not self.business_day)
            or (self.open_day and not self.trading_day)
        ):
            raise KisKrSessionCalendarEvidenceError
        return self


class KrSessionCalendarPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_commit: Literal["885dd4e2f5c37e4f7e23dd63c15555a9967bc7bc"]
    adapter_version: Literal["kis-chk-holiday-v1"]
    base_date: dt.date
    observed_at: dt.datetime
    receipt_sha256: str
    days: tuple[KrSessionDay, ...]

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        dates = tuple(day.session_date for day in self.days)
        if (
            not _aware(self.observed_at)
            or self.observed_at.astimezone(dt.timezone(dt.timedelta(hours=9))).date() != self.base_date
            or _HEX64.fullmatch(self.receipt_sha256) is None
            or not dates
            or dates[0] != self.base_date
            or dates != tuple(sorted(set(dates)))
        ):
            raise KisKrSessionCalendarEvidenceError
        return self


class KrSessionCalendarSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    snapshot_id: str
    payload: KrSessionCalendarPayload

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        expected = hashlib.sha256(canonical_experiment_ledger_json(self.payload).encode()).hexdigest()
        if self.snapshot_id != expected:
            raise KisKrSessionCalendarEvidenceError
        return self


def kr_session_calendar_snapshot(payload: KrSessionCalendarPayload) -> KrSessionCalendarSnapshot:
    validated = KrSessionCalendarPayload.model_validate(payload.model_dump(mode="python"))
    snapshot_id = hashlib.sha256(canonical_experiment_ledger_json(validated).encode()).hexdigest()
    return KrSessionCalendarSnapshot(snapshot_id=snapshot_id, payload=validated)


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None
