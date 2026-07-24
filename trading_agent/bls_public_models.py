from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self, assert_never, override

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.experiment_ledger_keys import canonical_experiment_ledger_json

BLS_PUBLIC_MAX_RAW_BYTES: Final = 4 * 1024 * 1024
_COLLECTION_ID = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
_SERIES_ID = re.compile(r"^[A-Z0-9_#-]{1,64}$")
_PERIOD = re.compile(r"^[A-Z][0-9]{2}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE = re.compile(r"^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$")


class BlsPublicStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class BlsPublicFailure(StrEnum):
    TRANSPORT = "transport"
    HTTP_STATUS = "http_status"
    RESPONSE_STRUCTURE = "response_structure"


class BlsPublicError(ValueError):
    @override
    def __str__(self) -> str:
        return "BLS public data evidence is invalid"


class BlsPublicRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    collection_id: str
    series_ids: tuple[str, ...] = Field(min_length=1, max_length=25)
    start_year: int
    end_year: int

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        if (
            _COLLECTION_ID.fullmatch(self.collection_id) is None
            or self.series_ids != tuple(sorted(set(self.series_ids)))
            or any(_SERIES_ID.fullmatch(value) is None for value in self.series_ids)
            or not 1900 <= self.start_year <= self.end_year <= 2100
            or self.end_year - self.start_year > 9
        ):
            raise BlsPublicError
        return self

    @property
    def request_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class BlsPublicRawResponse:
    request_id: str
    received_at: dt.datetime
    status_code: int
    content_type: str
    raw_payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            _SHA256.fullmatch(self.request_id) is None
            or not _aware(self.received_at)
            or not 100 <= self.status_code <= 599
            or _CONTENT_TYPE.fullmatch(self.content_type) is None
            or type(self.raw_payload) is not bytes
            or not 1 <= len(self.raw_payload) <= BLS_PUBLIC_MAX_RAW_BYTES
        ):
            raise BlsPublicError

    @property
    def receipt_id(self) -> str:
        material = "|".join(
            (
                self.request_id,
                self.received_at.astimezone(dt.UTC).isoformat(),
                str(self.status_code),
                self.content_type,
                hashlib.sha256(self.raw_payload).hexdigest(),
            )
        )
        return hashlib.sha256(material.encode()).hexdigest()


class BlsFootnote(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str | None = Field(default=None, min_length=1, max_length=16)
    text: str | None = Field(default=None, min_length=1, max_length=512)


class BlsObservation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int
    period: str
    period_name: str = Field(min_length=1, max_length=64)
    value: Decimal | None
    is_latest: bool
    footnotes: tuple[BlsFootnote, ...] = Field(max_length=16)

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if (
            not 1900 <= self.year <= 2100
            or _PERIOD.fullmatch(self.period) is None
            or (self.value is not None and not self.value.is_finite())
            or (
                self.value is None
                and not any(footnote.text is not None for footnote in self.footnotes)
            )
        ):
            raise BlsPublicError
        return self


class BlsSeriesSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    series_id: str
    observations: tuple[BlsObservation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_series(self) -> Self:
        keys = tuple((item.year, item.period) for item in self.observations)
        latest = tuple(item for item in self.observations if item.is_latest)
        if (
            _SERIES_ID.fullmatch(self.series_id) is None
            or len(keys) != len(set(keys))
            or keys != tuple(sorted(keys, reverse=True))
            or len(latest) > 1
            or (latest and latest[0] != self.observations[0])
        ):
            raise BlsPublicError
        return self


class BlsMacroSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request_id: str
    raw_receipt_id: str
    requested_series_ids: tuple[str, ...] = Field(min_length=1, max_length=25)
    start_year: int
    end_year: int
    observed_at: dt.datetime
    series: tuple[BlsSeriesSnapshot, ...] = Field(min_length=1, max_length=25)

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        observed_ids = tuple(item.series_id for item in self.series)
        if (
            _SHA256.fullmatch(self.request_id) is None
            or _SHA256.fullmatch(self.raw_receipt_id) is None
            or self.requested_series_ids != observed_ids
            or not _aware(self.observed_at)
            or not 1900 <= self.start_year <= self.end_year <= 2100
            or any(
                observation.year < self.start_year or observation.year > self.end_year
                for item in self.series
                for observation in item.observations
            )
        ):
            raise BlsPublicError
        return self

    @property
    def snapshot_id(self) -> str:
        return hashlib.sha256(canonical_experiment_ledger_json(self).encode()).hexdigest()

    @property
    def observation_count(self) -> int:
        return sum(len(item.observations) for item in self.series)

    @property
    def available_observation_count(self) -> int:
        return sum(
            observation.value is not None
            for item in self.series
            for observation in item.observations
        )

    @property
    def missing_observation_count(self) -> int:
        return self.observation_count - self.available_observation_count

    @property
    def observed_completeness_bps(self) -> int:
        return self.available_observation_count * 10_000 // self.observation_count


class BlsPublicRun(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    request: BlsPublicRequest
    started_at: dt.datetime
    completed_at: dt.datetime
    status: BlsPublicStatus
    failure: BlsPublicFailure | None
    receipt_id: str | None
    snapshot: BlsMacroSnapshot | None

    @model_validator(mode="after")
    def validate_run(self) -> Self:
        match self.status:
            case BlsPublicStatus.SUCCESS:
                variant_valid = self.failure is None and self.receipt_id is not None and self.snapshot is not None
            case BlsPublicStatus.FAILED:
                variant_valid = (
                    self.failure is not None
                    and self.snapshot is None
                    and (
                        (self.failure is BlsPublicFailure.TRANSPORT and self.receipt_id is None)
                        or (self.failure is not BlsPublicFailure.TRANSPORT and self.receipt_id is not None)
                    )
                )
            case unreachable:
                assert_never(unreachable)
        snapshot_valid = self.snapshot is None or (
            self.snapshot.request_id == self.request.request_id
            and self.snapshot.raw_receipt_id == self.receipt_id
            and self.snapshot.requested_series_ids == self.request.series_ids
            and self.snapshot.start_year == self.request.start_year
            and self.snapshot.end_year == self.request.end_year
            and self.started_at <= self.snapshot.observed_at <= self.completed_at
        )
        if (
            not variant_valid
            or not snapshot_valid
            or not _aware(self.started_at)
            or not _aware(self.completed_at)
            or self.completed_at < self.started_at
            or (self.receipt_id is not None and _SHA256.fullmatch(self.receipt_id) is None)
        ):
            raise BlsPublicError
        return self

    @property
    def run_id(self) -> str:
        return hashlib.sha256(f"bls-public|{self.request.request_id}".encode()).hexdigest()


def _aware(value: dt.datetime) -> bool:
    return type(value) is dt.datetime and value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "BLS_PUBLIC_MAX_RAW_BYTES",
    "BlsFootnote",
    "BlsMacroSnapshot",
    "BlsObservation",
    "BlsPublicError",
    "BlsPublicFailure",
    "BlsPublicRawResponse",
    "BlsPublicRequest",
    "BlsPublicRun",
    "BlsPublicStatus",
    "BlsSeriesSnapshot",
)
